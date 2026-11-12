"""Read-only archive audit: missing/moved files, duplicate content and disk usage per project.

`outputs/` is durable user data, so this module never deletes, moves or rewrites anything.
It compares each project manifest with what is actually on disk and produces a report the user
can read before deciding what to do. Large media is not re-hashed: duplicates are judged from
the SHA-256 recorded when a resource was imported.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import time

from video_parser.services import library

MAX_ITEMS = 200
SIDECAR = 'personal-learning'
# Duplicate *downloads* matter for media and text sources; identical reports or frames are not a download.
DUPLICATE_KINDS = {'video', 'subtitle', 'transcription', 'translation'}


def _linked(path):
    return path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)())


def _walk(directory):
    """Every regular file under a project directory, links and transient dot-files excluded."""
    files = {}
    for root, dirs, names in os.walk(directory, followlinks=False):
        base = Path(root)
        dirs[:] = [name for name in dirs if not _linked(base / name)]
        for name in names:
            if name.startswith('.'):
                continue
            path = base / name
            if _linked(path):
                continue
            try:
                files[path.relative_to(directory).as_posix()] = path.stat().st_size
            except (OSError, ValueError):
                continue
    return files


def _scan(project):
    directory = project['directory']
    actual = _walk(directory)
    registered = {}
    missing, changed, unsafe = [], [], []
    for name, info in (project.get('resources') or {}).items():
        if not isinstance(info, dict):
            continue
        try:
            path = library.resource_path(project, name)
            relative = path.relative_to(directory).as_posix()
        except (KeyError, ValueError):
            unsafe.append({'project_id': project.get('id'), 'title': project.get('title') or '', 'resource': name,
                           'path': str(name), 'reason': '登记路径不安全或不属于当前项目'})
            continue
        recorded = info.get('size_bytes') if type(info.get('size_bytes')) is int else None
        entry = {'project_id': project.get('id'), 'title': project.get('title') or '', 'resource': name,
                 'path': relative, 'kind': info.get('kind'), 'recorded_bytes': recorded}
        registered[relative] = {**entry, 'sha256': info.get('sha256'), 'present': relative in actual,
                                'actual_bytes': actual.get(relative)}
        if relative not in actual and info.get('cloud_missing') is not True:
            missing.append(entry)
        elif relative in actual and recorded is not None and actual[relative] != recorded:
            changed.append({**entry, 'actual_bytes': actual[relative]})
    sidecars = {name: size for name, size in actual.items() if name.startswith(SIDECAR + '/')}
    orphans = [{'project_id': project.get('id'), 'title': project.get('title') or '', 'path': name, 'size_bytes': size}
               for name, size in actual.items()
               if name not in registered and name != 'project.json' and name not in sidecars]
    # A missing file with exactly one same-named unregistered file was probably moved by hand.
    by_name = {}
    for row in orphans:
        by_name.setdefault(Path(row['path']).name, []).append(row['path'])
    for row in missing:
        candidates = by_name.get(Path(row['path']).name, [])
        if len(candidates) == 1:
            row['suspected_move'] = candidates[0]
    resource_bytes = sum(size for name, size in actual.items() if name in registered)
    orphan_bytes = sum(row['size_bytes'] for row in orphans)
    row = {'id': project.get('id'), 'title': project.get('title') or '',
           'platform': next((label for label, slug in library.PLATFORMS.items() if slug == project.get('platform')),
                            project.get('platform') or ''),
           'location': project['directory'].relative_to(library.LIBRARY_ROOT).as_posix(),
           'files': len(registered), 'disk_bytes': sum(actual.values()), 'resource_bytes': resource_bytes,
           'orphan_bytes': orphan_bytes, 'sidecar_bytes': sum(sidecars.values()),
           'missing': len(missing), 'changed': len(changed), 'orphans': len(orphans),
           'issues': len(missing) + len(changed) + len(orphans) + len(unsafe)}
    return row, missing, changed, orphans, unsafe, registered


def _listing(rows):
    return {'items': rows[:MAX_ITEMS], 'total': len(rows), 'shown': min(len(rows), MAX_ITEMS)}


def inspect():
    started = time.monotonic()
    projects, missing, changed, orphans, unsafe, unreadable = [], [], [], [], [], []
    hashes, sizes = {}, {}
    for manifest in library.manifests():
        try:
            project = library.load(manifest)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            unreadable.append({'path': str(manifest), 'error': type(error).__name__})
            continue
        row, absent, altered, extra, blocked, registered = _scan(project)
        projects.append(row)
        missing += absent
        changed += altered
        orphans += extra
        unsafe += blocked
        for relative, entry in registered.items():
            digest = entry.get('sha256')
            if (entry['present'] and entry.get('kind') in DUPLICATE_KINDS
                    and isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest)):
                sizes.setdefault(digest, entry.get('actual_bytes') or entry.get('recorded_bytes') or 0)
                hashes.setdefault(digest, []).append({'project_id': entry['project_id'], 'title': entry['title'],
                                                      'resource': entry['resource'], 'path': relative,
                                                      'kind': entry.get('kind')})
    duplicates = [{'sha256': digest, 'size_bytes': sizes.get(digest, 0), 'entries': entries}
                  for digest, entries in hashes.items() if len(entries) > 1]
    duplicates.sort(key=lambda group: group['size_bytes'] * (len(group['entries']) - 1), reverse=True)
    duplicate_bytes = sum(group['size_bytes'] * (len(group['entries']) - 1) for group in duplicates)
    projects.sort(key=lambda row: row['disk_bytes'], reverse=True)
    missing.sort(key=lambda row: row.get('recorded_bytes') or 0, reverse=True)
    changed.sort(key=lambda row: row.get('recorded_bytes') or 0, reverse=True)
    orphans.sort(key=lambda row: row['size_bytes'], reverse=True)
    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'duration_ms': int((time.monotonic() - started) * 1000),
        'archive_root': str(library.LIBRARY_ROOT.resolve()),
        'totals': {'projects': len(projects), 'files': sum(row['files'] for row in projects),
                   'disk_bytes': sum(row['disk_bytes'] for row in projects),
                   'resource_bytes': sum(row['resource_bytes'] for row in projects),
                   'orphan_bytes': sum(row['orphan_bytes'] for row in projects),
                   'sidecar_bytes': sum(row['sidecar_bytes'] for row in projects),
                   'missing': len(missing), 'changed': len(changed),
                   'orphan_files': len(orphans), 'unsafe': len(unsafe),
                   'duplicate_groups': len(duplicates), 'duplicate_bytes': duplicate_bytes,
                   'unreadable': len(unreadable)},
        'projects': projects,
        'issues': {'missing': _listing(missing), 'changed': _listing(changed), 'orphans': _listing(orphans),
                   'unsafe': _listing(unsafe), 'unreadable': _listing(unreadable),
                   'duplicates': {**_listing(duplicates), 'items': [
                       {**group, 'copies': len(group['entries']), 'entries': group['entries'][:20]}
                       for group in duplicates[:MAX_ITEMS]]}},
        'read_only': True,
    }
    return report


def _size(value):
    size = float(value or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


def _when(value):
    try:
        return datetime.fromisoformat(value).astimezone().strftime('%Y-%m-%d %H:%M')
    except (TypeError, ValueError):
        return value or '未记录'


def export_markdown(report):
    """A report meant to be read and shared, not a cleanup script."""
    totals = report['totals']
    lines = ['# 归档体检报告', '',
             f"- 生成时间：{_when(report['generated_at'])}",
             f"- 归档位置：`{report['archive_root']}`",
             f"- 扫描耗时：{report['duration_ms']} 毫秒",
             '', '> 只读体检：不会删除、移动、改名或修复任何文件。', '',
             '## 概要', '',
             '| 项目 | 登记文件 | 磁盘占用 | 登记资源 | 未登记文件 | 缺失 | 大小变化 | 重复组 |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |',
             f"| {totals['projects']} | {totals['files']} | {_size(totals['disk_bytes'])} | {_size(totals['resource_bytes'])} "
             f"| {_size(totals['orphan_bytes'])}（{totals['orphan_files']} 个） | {totals['missing']} | {totals['changed']} "
             f"| {totals['duplicate_groups']}（多占 {_size(totals['duplicate_bytes'])}） |", '']
    if report['projects']:
        lines += ['## 按项目统计', '', '| 项目 | 平台 | 磁盘占用 | 文件 | 缺失 | 变化 | 未登记 |', '| --- | --- | --- | --- | --- | --- | --- |']
        for row in report['projects'][:100]:
            lines.append(f"| {row['title']} | {row['platform']} | {_size(row['disk_bytes'])} | {row['files']} "
                         f"| {row['missing']} | {row['changed']} | {_size(row['orphan_bytes'])} |")
        if len(report['projects']) > 100:
            lines.append(f"| … | 其余 {len(report['projects']) - 100} 个项目未列出 | | | | | |")
        lines.append('')

    def section(title, listing, row):
        if not listing['total']:
            return
        lines.extend([f'## {title}', ''])
        for item in listing['items']:
            lines.append(row(item))
        if listing['total'] > listing['shown']:
            lines.append(f"- 共 {listing['total']} 项，仅列出前 {listing['shown']} 项。")
        lines.append('')

    section('缺失文件（记录存在，磁盘上找不到）', report['issues']['missing'],
            lambda row: f"- {row['title']} · `{row['path']}`（记录 "
                        f"{_size(row['recorded_bytes']) if row.get('recorded_bytes') is not None else '大小未记录'}）"
                        + (f"，可能已移动到 `{row['suspected_move']}`" if row.get('suspected_move') else ''))
    section('大小变化的文件（可能被截断或替换）', report['issues']['changed'],
            lambda row: f"- {row['title']} · `{row['path']}`：记录 "
                        f"{_size(row['recorded_bytes']) if row.get('recorded_bytes') is not None else '大小未记录'}，"
                        f"实际 {_size(row.get('actual_bytes'))}")
    section('重复内容（相同 SHA-256，可人工确认后处理）', report['issues']['duplicates'],
            lambda row: f"- {_size(row['size_bytes'])} · " + '；'.join(
                f"{entry['title']} · `{entry['path']}`" for entry in row['entries']))
    section('未登记文件（不在项目清单里，可能是手动放入或改名遗留）', report['issues']['orphans'],
            lambda row: f"- {row['title']} · `{row['path']}`（{_size(row['size_bytes'])}）")
    section('路径不安全的登记项', report['issues']['unsafe'],
            lambda row: f"- {row['title']} · `{row['path']}`：{row['reason']}")
    section('无法读取的项目清单', report['issues']['unreadable'],
            lambda row: f"- `{row['path']}`：{row['error']}")
    lines += ['## 说明', '',
              '- 重复判断使用归档清单中的 SHA-256，不重新读取大文件，因此不会因扫描而长时间占用磁盘。',
              '- “未登记文件”不等于垃圾：可能是手动放入的素材、旧版本或改名遗留，请先核对再处理。',
              '- 本报告不提供一键清理；删除任何文件前请自行备份。', '']
    return '\n'.join(lines), f"归档体检_{datetime.now().strftime('%Y%m%d-%H%M')}"
