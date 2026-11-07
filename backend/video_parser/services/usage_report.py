"""Aggregate model usage already stored with archived analyses, follow-ups and conversations.

Nothing is measured twice and nothing is invented: every number comes from a manifest entry
written when the call finished. Translation, naming and title generation do not persist token
usage yet, so they are reported as uncovered instead of being guessed from text length.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from video_parser.services import ai_settings, library

KIND_LABELS = {'analysis': '分析', 'followup': '追问', 'chat': '对话', 'planning': '选帧规划', 'translation': '翻译'}
MAX_PROJECTS = 50
MAX_MODELS = 50


def _count(value):
    return value if type(value) is int and value >= 0 else 0


def _tokens(usage):
    if not isinstance(usage, dict):
        return 0, 0
    return _count(usage.get('prompt_tokens')), _count(usage.get('completion_tokens'))


def _engine(info):
    agent = info.get('agent')
    if isinstance(agent, dict) and agent.get('engine') in ('api', 'claude', 'codex', 'opencode', 'dsh'):
        return agent['engine']
    return 'api'  # Records written before agents were selectable came from the configured API.


def _when(value, name=''):
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            moment = None
        if moment is not None:
            return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)
    match = re.match(r'(\d{8})-(\d{6})', Path(str(name)).stem)
    if match:
        try:
            return datetime.strptime(match[1] + match[2], '%Y%m%d%H%M%S').astimezone(timezone.utc)
        except ValueError:
            pass
    return None


def _events():
    for manifest in library.manifests():
        try:
            project = library.load(manifest)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        resources = project.get('resources')
        if not isinstance(resources, dict):
            continue
        for name, info in resources.items():
            if not isinstance(info, dict):
                continue
            kind = info.get('kind')
            if kind == 'analysis':
                yield from _rows(project, name, info, 'analysis', info.get('usage'), _engine(info))
                if info.get('planning_usage'):
                    yield from _rows(project, name, info, 'planning', info.get('planning_usage'), _engine(info))
            elif kind == 'analysis-followup':
                yield from _rows(project, name, info, 'followup', info.get('usage'), _engine(info))
            elif kind == 'translation':
                # Archived translations carry the per-batch sum; unarchived ones have no manifest entry.
                yield from _rows(project, name, info, 'translation', info.get('usage'), _engine(info))
            elif kind == 'conversation-message':
                yield from _rows(project, name, info, 'chat', info.get('usage'), _engine(info))
                context = info.get('context')
                planner = context.get('planner_usage') if isinstance(context, dict) else None
                if planner:
                    yield from _rows(project, name, info, 'planning', planner, _engine(info))


def _rows(project, name, info, kind, usage, engine):
    prompt, completion = _tokens(usage)
    if not prompt and not completion:
        return
    yield {'at': _when(info.get('created_at'), name), 'kind': kind,
           'model': str(info.get('model') or '').strip() or '未记录', 'engine': engine,
           'prompt_tokens': prompt, 'completion_tokens': completion,
           'project_id': project.get('id'), 'project_title': project.get('title') or '未命名项目'}


def _cost(event, price_input, price_output):
    """(cost, priced). Native agents are subscription-based; missing prices stay unknown."""
    if event['engine'] != 'api':
        return 0.0, True
    if price_input is None or price_output is None:
        return None, False
    return event['prompt_tokens'] / 1_000_000 * price_input + event['completion_tokens'] / 1_000_000 * price_output, True


def _bucket():
    return {'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0,
            'cost': 0.0, 'unpriced_calls': 0, 'native_calls': 0}


def _add(bucket, event, cost, priced):
    bucket['calls'] += 1
    bucket['prompt_tokens'] += event['prompt_tokens']
    bucket['completion_tokens'] += event['completion_tokens']
    bucket['total_tokens'] += event['prompt_tokens'] + event['completion_tokens']
    if event['engine'] != 'api':
        bucket['native_calls'] += 1
    elif priced:
        bucket['cost'] += cost
    else:
        bucket['unpriced_calls'] += 1


def _finish(bucket):
    """A total is unknown when any API call lacks a configured price."""
    bucket['cost'] = None if bucket['unpriced_calls'] else round(bucket['cost'], 6)
    return bucket


def collect(days=30):
    days = max(0, int(days or 0))
    settings = ai_settings.current()
    price_input, price_output = settings.get('price_input'), settings.get('price_output')
    local_now = datetime.now().astimezone()
    cutoff = local_now - timedelta(days=days) if days else None
    totals, by_kind, by_model, by_project, by_day = _bucket(), {}, {}, {}, {}
    projects = set()
    for event in _events():
        if cutoff and (event['at'] is None or event['at'] < cutoff):
            continue
        cost, priced = _cost(event, price_input, price_output)
        _add(totals, event, cost, priced)
        projects.add(event['project_id'])
        kind = by_kind.setdefault(event['kind'], {**_bucket(), 'kind': event['kind'], 'label': KIND_LABELS.get(event['kind'], event['kind'])})
        _add(kind, event, cost, priced)
        model = by_model.setdefault(event['model'], {**_bucket(), 'model': event['model'], 'engines': []})
        _add(model, event, cost, priced)
        if event['engine'] not in model['engines']:
            model['engines'].append(event['engine'])
        project = by_project.setdefault(event['project_id'], {**_bucket(), 'id': event['project_id'], 'title': event['project_title']})
        _add(project, event, cost, priced)
        if event['at'] is not None:
            date = event['at'].astimezone().date().isoformat()
            day = by_day.setdefault(date, {'date': date, 'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0})
            day['calls'] += 1
            day['prompt_tokens'] += event['prompt_tokens']
            day['completion_tokens'] += event['completion_tokens']
            day['total_tokens'] += event['prompt_tokens'] + event['completion_tokens']
    if 0 < days <= 90:
        for offset in range(days - 1, -1, -1):
            date = (local_now - timedelta(days=offset)).date().isoformat()
            by_day.setdefault(date, {'date': date, 'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0})
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'window_days': days,
        'currency': 'CNY',
        'prices': {'input': price_input, 'output': price_output,
                   'configured': price_input is not None and price_output is not None},
        'totals': {**_finish(totals), 'projects': len(projects)},
        'by_kind': [_finish(row) for row in sorted(by_kind.values(), key=lambda row: -row['total_tokens'])],
        'by_model': [_finish(row) for row in sorted(by_model.values(), key=lambda row: -row['total_tokens'])[:MAX_MODELS]],
        'by_project': [_finish(row) for row in sorted(by_project.values(), key=lambda row: -row['total_tokens'])[:MAX_PROJECTS]],
        'by_day': sorted(by_day.values(), key=lambda row: row['date']),
        'coverage': '仅统计已归档的分析、追问、对话、选帧规划，以及已归档到项目的 AI 翻译；旧版记录和未归档到项目的翻译、命名、对话标题生成没有保存 token 用量，因此不计入。费用按当前单价估算，实际账单以服务商为准。',
    }
