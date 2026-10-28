"""Focused Playwright regression for Bilibili learning progress in the built React workbench.

Run after ``npm --prefix frontend run build``. The workspace API is mocked; backend
learning-state persistence is covered separately by ``tests/offline/test_react_api.py``.
"""
import io
import os
import json
import wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VIDEO_ID = 'BV1TaqYBcEJc'
PARTS = [(1, '第一讲', 75), (2, '核心内容', 180), (3, '总结', 120)]


def main(timeline_test=False, study_test=False, agent_test=False):
    dist = Path(os.environ.get('VIDEO_PARSER_TEST_DIST', ROOT / 'frontend' / 'dist'))
    assert (dist / 'index.html').is_file(), 'Build the React frontend first'
    state = {'learned': {}, 'created': set(), 'requests': [], 'parse_calls': 0, 'with_media': False, 'study': {}, 'fail_notes': False, 'fail_materials': False, 'analysis_calls': 0}

    class StaticHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dist), **kwargs)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), StaticHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    ai = {
        'provider': 'fixture', 'provider_name': '浏览器测试', 'base_url': 'https://fixture.invalid/v1',
        'model': 'fixture', 'key_saved': False, 'key_hint': '', 'price_input': None, 'price_output': None,
        'configured': timeline_test, 'local': timeline_test, 'consented': timeline_test, 'source': 'none', 'storage': 'fixture', 'presets': {},
    }
    config = {
        'platforms': ['哔哩哔哩'], 'ai_configured': timeline_test, 'media_ready': True, 'subtitles_ready': True,
        'transcription_ready': True, 'transcription_upload_limit': 1000000,
        'archive_root': 'mock/library', 'ai': ai,
    }
    analysis_context = {
        'transcripts': [], 'duration': 375, 'auto_frame_count': 8, 'image_tokens': 800,
        'prompt_tokens': 700, 'output_tokens': 1500,
        'templates': [{'id': 'summary', 'name': '内容摘要', 'description': '提炼要点', 'source': 'both', 'requires': None}],
        'sources': {'both': '字幕 + 画面', 'transcript': '只用字幕', 'frames': '只用画面'}, 'ai': ai,
    }

    agent_catalog = {'permission':'restricted-learning','engines':[
        {'id':engine,'name':name,'installed':True,'discovered':True,'updated_at':1,
         'models':[{'id':engine+'-fixture','label':name+' fixture','efforts':['low','high'] if engine!='opencode' else [],'images':True}],
         'commands':[{'name':'fixture-skill','prefix':'$' if engine=='codex' else '/','description':'Offline fixture'}] if engine!='opencode' else [],'error':''}
        for engine,name in [('claude','Claude Code'),('codex','Codex'),('opencode','OpenCode')]]}
    state['agent_calls'], state['agent_probes'] = [], []
    records = {}
    conversations = {}
    if timeline_test:
        analysis_context['templates'].append({'id': 'chapters', 'name': '章节时间轴', 'description': '章节测试', 'source': 'transcript', 'requires': 'transcript'})
        analysis_context['transcripts'] = [{'resource': 'subtitles/fixture.srt', 'label': '平台字幕', 'kind': 'platform', 'language': 'zh', 'cues': 20, 'chars': 100, 'merged': False, 'tokens': 100, 'end': '00:20'}]

    def episode_rows():
        return [{
            'part': part, 'title': title, 'duration': duration,
            'project_id': f'{part:032x}' if part in state['created'] else None,
            'archived': False, 'learned': state['learned'].get(part, False),
        } for part, title, duration in PARTS]

    audio = io.BytesIO()
    with wave.open(audio, 'wb') as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(8000)
        clip.writeframes(b'\0\0' * 8000 * 20)

    def serve_media(route):
        body = audio.getvalue()
        headers = {'Accept-Ranges': 'bytes'}
        requested = route.request.headers.get('range', '')
        if requested.startswith('bytes='):
            first, last = requested.removeprefix('bytes=').split('-', 1)
            start = int(first or 0)
            end = min(int(last) if last else len(body) - 1, len(body) - 1)
            headers['Content-Range'] = f'bytes {start}-{end}/{len(body)}'
            return route.fulfill(status=206, content_type='audio/wav',
                                 headers=headers, body=body[start:end + 1])
        return route.fulfill(content_type='audio/wav', headers=headers, body=body)

    def parsed_video(part):
        state['created'].add(part)
        project_id = f'{part:032x}'
        project = {
            'id': project_id, 'title': '本地多分集学习测试', 'platform': '哔哩哔哩', 'part': part,
            'location': f'bilibili/{VIDEO_ID}/P{part}', 'identity_status': 'verified',
            'learned': state['learned'].get(part, False), 'archive_url': f'/api/workspace/projects/{project_id}/archive',
            'resources': [],
        }
        media = {
            'resource': 'video/1080p.wav', 'quality_id': '1080p',
            'preview_url': '/learning-fixture.wav', 'download_url': '/learning-fixture.wav',
            'filename': 'fixture.wav', 'size_bytes': len(audio.getvalue()),
            'duration': 20, 'width': None, 'height': None,
        } if state['with_media'] else None
        return {
            'id': project_id, 'title': '本地多分集学习测试', 'platform': '哔哩哔哩',
            'source_url': f'https://www.bilibili.com/video/{VIDEO_ID}?p={part}',
            'cover_url': None, 'cover_download_url': None, 'warning': None,
            'project': project, 'media': media,
            'qualities': [{'id': '1080p', 'label': '1080p', 'width': 1920, 'height': 1080, 'media': media}],
            'default_quality_id': '1080p', 'bilibili_cookie_status': 'available', 'duration': 375,
            'episodes': episode_rows(),
        }

    def api(route):
        request = route.request
        path = urlsplit(request.url).path.removeprefix('/api/workspace')
        state['requests'].append((request.method, path))
        body = request.post_data_json if request.method == 'POST' and request.post_data else {}
        if '/study/' in path:
            identity = path.split('/')[2]
            resource = body.get('resource') if request.method == 'POST' else parse_qs(urlsplit(request.url).query)['resource'][0]
            entry = state['study'].setdefault((identity, resource), {'progress': {'position':0,'duration':20,'revision':0}, 'notes':{}})
            if path.endswith('/progress'):
                if request.method == 'GET': return route.fulfill(json=entry['progress'])
                if body['expected_revision'] != entry['progress']['revision']: return route.fulfill(status=409,json={'detail':'播放位置已变化'})
                entry['progress'] = {'position':body['position'],'duration':body['duration'],'revision':entry['progress']['revision']+1}
                return route.fulfill(json=entry['progress'])
            if path.endswith('/notes'): return route.fulfill(json={'notes':list(entry['notes'].values())})
            if state['fail_notes']: return route.fulfill(status=503,json={'detail':'模拟保存失败，草稿保留'})
            note_id = path.split('/')[-2] if path.endswith('/delete') else path.split('/')[-1]
            previous = entry['notes'].get(note_id)
            if (previous or {}).get('revision',0) != body['expected_revision']: return route.fulfill(status=409,json={'detail':'笔记已更新，草稿保留'})
            if path.endswith('/delete'):
                entry['notes'].pop(note_id,None);return route.fulfill(json={'deleted':note_id})
            note = {'id':note_id,'time':body['time'],'text':body['text'].strip(),'revision':body['expected_revision']+1}
            entry['notes'][note_id] = note;return route.fulfill(json=note)
        if path == '/ai/agents':
            return route.fulfill(json=agent_catalog)
        if path == '/ai/agents/discover':
            assert body['confirm'] is True and set(body) <= {'confirm','engine','model'}, body
            state['agent_probes'].append(body)
            return route.fulfill(json=agent_catalog)
        if path == '/config':
            return route.fulfill(json=config)
        if path == '/tasks':
            return route.fulfill(json={'jobs': []})
        if path == '/accounts':
            return route.fulfill(json={'platforms': [], 'storage': 'fixture'})
        if path == '/transcriptions/sources':
            return route.fulfill(json={'sources': []})
        if path == '/projects':
            return route.fulfill(json={'projects': []})
        if path == '/folders':
            return route.fulfill(json={'folders': []})
        if '/conversations' in path:
            project_id = path.split('/')[2]
            sessions = conversations.setdefault(project_id, [])
            if path.endswith('/conversations/context'):
                missing = state.get('chat_missing')
                if missing is None:
                    kinds = {row['template'] for row in records.get(project_id, []) if row['video_resource'] == 'video/1080p.wav'}
                    missing = ([] if analysis_context['transcripts'] else ['subtitles']) + [k for k in ['chapters','summary'] if k not in kinds]
                return route.fulfill(json={'available': not missing, 'missing': missing})
            if path.endswith('/migrate') or (path.endswith('/conversations') and request.method == 'GET'):
                return route.fulfill(json={'conversations': sessions})
            if path.endswith('/conversations') and request.method == 'POST':
                item = {'id': f'{len(sessions)+1:032x}', 'title':'新对话', 'title_generated':False,
                        'created_at':'2026-10-17T00:00:00Z', 'updated_at':'2026-10-17T00:00:00Z',
                        'video_resource':body['resource'], 'messages':[], 'agent':body.get('agent') or {'engine':'api','model':'','effort':''}}
                sessions.insert(0,item)
                return route.fulfill(json={'conversation':item})
            if path.endswith('/ask-task'):
                item = next(x for x in sessions if x['id']==path.split('/')[4])
                assert body['consent'] is True and body['question'].strip(), body
                assert 0 <= body['position'] <= 20 and body['resource']=='video/1080p.wav', body
                answer = {'id':f'f{len(item["messages"])+1}', 'question':body['question'],
                          'answer':'模拟回答：核心概念从 [00:05] 开始。', 'created_at':'2026-10-17T00:00:01Z'}
                target = body.get('agent') or {'engine':'api','model':'','effort':''}
                assert target['engine'] == item['agent']['engine'], body
                if target['engine'] != 'api':
                    assert body['consent'] is True
                    state['agent_calls'].append({'target':target,'conversation':item['id'],'question':body['question'],'position':body['position']})
                item['messages'].append(answer)
                item['title'] = {'claude':'Claude Code 测试对话','codex':'Codex 测试对话','opencode':'OpenCode 测试对话'}.get(target['engine'],'核心概念讨论')
                item['title_generated']=True
                return route.fulfill(status=202,json={'id':'c'*32,'state':'completed','message':'模拟回答完成',
                    'result':{'text':answer['answer'],'conversation':item,'mode':'conversation','project':parsed_video(int(project_id,16))['project']}})
        if path.endswith('/analysis/context'):
            if state['fail_materials']: return route.fulfill(status=503,json={'detail':'模拟材料读取失败'})
            return route.fulfill(json=analysis_context)
        if path.endswith('/analyses'):
            return route.fulfill(json={'records': records.get(path.split('/')[2], [])})
        if timeline_test and path.startswith('/videos/') and path.endswith('/analyze-task'):
            assert body['template'] == 'chapters' and body['source'] == 'transcript', body
            if agent_test and not ai['configured']:
                assert body.get('agent',{}).get('engine')=='codex' and body['consent'] is True,body
            part = int(path.split('/')[2], 16)
            record = {'id': '20261015-202500-abcd0001', 'created_at': '2026-10-15T20:25:00', 'template': 'chapters', 'template_name': '章节时间轴',
                      'source': 'transcript', 'source_label': '只用字幕', 'prompt': '', 'model': 'fixture', 'provider': 'fixture',
                      'transcript_label': '平台字幕', 'transcript_merged': False, 'video_resource': 'video/1080p.wav',
                      'frame_count': 0, 'frames': [], 'usage': {}, 'text': '[00:00] 开场 —— 说明学习目标。\n[00:05] 核心概念 —— 理解核心概念和使用方法。\n[00:12] 总结\n[99:99] 无效时间',
                      'followups': [], 'legacy': False}
            plain = {**record, 'id': '20261015-202400-abcd0002', 'template': 'summary', 'template_name': '内容摘要', 'text': '没有章节时间点的摘要。'}
            other = {**record, 'id': '20261015-202300-abcd0003', 'video_resource': 'video/other.wav'}
            later = {**record, 'id': '20261015-202200-abcd0004', 'text': '[00:05] 新版章节 —— 新记录的介绍。'}
            state['analysis_calls'] += 1
            if state['analysis_calls'] > 1:
                record = {**record, 'id':'20261016-003000-abcd0005', 'text':'[00:00] 新生成材料 —— 新一轮生成的结果。'}
                records[path.split('/')[2]] = [record, *records[path.split('/')[2]]]
            else:
                records[path.split('/')[2]] = [record, plain, other, later]
            return route.fulfill(status=202, json={'id': 'b'*32, 'state': 'completed', 'message': '模拟分析完成',
                 'result': {'text': record['text'], 'record': record, 'mode': 'analyze', 'project': parsed_video(part)['project']}})
        if timeline_test and path.startswith('/projects/') and path.endswith('/clear-context'):
            project_id, record_id = path.split('/')[2], path.split('/')[4]
            rows = records[project_id]
            index = next(i for i, row in enumerate(rows) if row['id'] == record_id)
            rows[index] = {**rows[index], 'context_after': rows[index]['followups'][-1]['id']}
            return route.fulfill(json={'records': rows})
        if timeline_test and path.startswith('/projects/') and path.endswith('/ask-task'):
            project_id, record_id = path.split('/')[2], path.split('/')[4]
            assert body['consent'] is True and body['question'].strip(), body
            rows = records[project_id]
            index = next(i for i, row in enumerate(rows) if row['id'] == record_id)
            answer = {'id': f'f{len(rows[index]["followups"]) + 1}', 'question': body['question'],
                      'answer': '模拟回答：核心概念从 [00:05] 开始。', 'created_at': '2026-10-16T09:00:00'}
            rows[index] = {**rows[index], 'followups': [*rows[index]['followups'], answer]}
            return route.fulfill(status=202, json={'id': 'c'*32, 'state': 'completed', 'message': '模拟追问完成',
                 'result': {'text': answer['answer'], 'record': rows[index], 'mode': 'ask', 'project': parsed_video(int(project_id, 16))['project']}})
        if path == '/parse' and request.method == 'POST':
            source = body.get('text', '')
            part = int(parse_qs(urlsplit(source).query).get('p', ['1'])[0])
            state['parse_calls'] += 1
            return route.fulfill(json=parsed_video(part))
        if path.startswith('/projects/') and path.endswith('/resume'):
            return route.fulfill(json=parsed_video(int(path.split('/')[2], 16)))
        if path.startswith('/projects/') and path.endswith('/learning') and request.method == 'POST':
            part = body.get('part')
            if type(part) is int and part in {item[0] for item in PARTS} and type(body.get('learned')) is bool:
                state['learned'][part] = body['learned']
                return route.fulfill(json={'episodes': episode_rows()})
            return route.fulfill(status=422, json={'detail': 'invalid fixture request'})
        return route.fulfill(status=404, json={'detail': f'Unexpected fixture request: {request.method} {path}'})

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel=os.environ.get('VIDEO_PARSER_BROWSER_CHANNEL'))
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/workspace/**', api)
            page.route('**/learning-fixture.wav', serve_media)
            url = f'http://127.0.0.1:{server.server_port}'
            page.goto(url + '?page=unknown', wait_until='networkidle')
            expect(page.get_by_role('heading', name='视频工作台', exact=True)).to_be_visible()
            learning_nav = page.locator('.app-sidebar').get_by_role('button', name='学习视图', exact=True)
            expect(learning_nav).to_be_visible()
            expect(page.locator('.app-sidebar nav > button')).to_have_count(5)
            learning_nav.click()
            expect(learning_nav).to_have_attribute('aria-current', 'page')
            expect(page.get_by_test_id('page-learning')).to_be_visible()
            expect(page.get_by_role('heading', name='学习视图', exact=True)).to_be_visible()
            expect(page.get_by_role('tablist')).to_be_hidden()
            expect(page.locator('.learning-notes-panel')).to_be_visible()
            assert parse_qs(urlsplit(page.url).query)['page'] == ['learning']
            expect(page.locator('.analysis-form')).to_have_count(0)
            expect(page.locator('#source')).to_be_hidden()
            page.get_by_role('button', name='去准备视频', exact=True).click()
            source = page.locator('#source')
            source.fill(f'https://www.bilibili.com/video/{VIDEO_ID}')
            page.get_by_role('button', name='解析视频', exact=True).click()
            learning_nav.click()
            expect(learning_nav).to_have_attribute('aria-current', 'page')
            def open_part(part):
                page.locator('.app-sidebar').get_by_role('button', name='视频工作台', exact=True).click()
                page.get_by_role('tab', name='视频', exact=True).click()
                page.get_by_role('button', name='清空链接', exact=True).click()
                source.fill(f'https://www.bilibili.com/video/{VIDEO_ID}?p={part}')
                page.get_by_role('button', name='解析视频', exact=True).click()
                expect(page.locator('.info-title')).to_contain_text('本地多分集学习测试')
                learning_nav.click()


            expect(page.get_by_role('heading', name='分集学习', exact=True)).to_be_visible()
            expect(page.get_by_text('已学完 0 / 3', exact=True)).to_be_visible()
            assert page.get_by_text('整理审批', exact=False).count() == 0
            player_p1 = page.get_by_label('当前这一讲 P1 已学完')
            page.get_by_label('标记 P1「第一讲」已学完').click()
            expect(player_p1).to_be_checked()
            expect(page.get_by_text('已学完 1 / 3', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='继续学 P2', exact=True)).to_be_visible()

            page.get_by_role('button', name='继续学 P2', exact=True).click()
            player_p2 = page.get_by_label('当前这一讲 P2 已学完')
            expect(player_p2).to_be_visible()
            expect(player_p2).not_to_be_checked()
            expect(page.get_by_role('combobox', name='学习项目', exact=True)).to_contain_text('P2')
            expect(page.get_by_label('标记 P1「第一讲」已学完')).to_be_checked()
            page.get_by_label('标记 P2「核心内容」已学完').click()
            expect(player_p2).to_be_checked()
            expect(page.get_by_text('已学完 2 / 3', exact=True)).to_be_visible()

            # Reload uses the existing project resume API, without reparsing/downloading.
            page.reload(wait_until='networkidle')
            expect(page.get_by_test_id('page-learning')).to_be_visible()
            expect(page.get_by_text('已学完 2 / 3', exact=True)).to_be_visible()
            expect(page.get_by_label('当前这一讲 P2 已学完')).to_be_checked()
            page.get_by_role('button', name='生成/管理 AI 学习材料', exact=True).click()
            expect(page.locator('#video-stage')).to_be_hidden()
            expect(page.locator('#source')).to_be_hidden()
            expect(page.get_by_role('button', name='开始分析', exact=True)).to_be_disabled()
            frame_mode = page.get_by_label('选取画面的方式')
            frame_mode.select_option('uniform')
            workbench_nav = page.locator('.app-sidebar').get_by_role('button', name='视频工作台', exact=True)
            workbench_nav.click()
            expect(workbench_nav).to_have_attribute('aria-current', 'page')
            expect(page.get_by_role('tab', name='AI 分析')).to_have_attribute('aria-selected', 'true')
            page.get_by_role('tab', name='AI 分析').click()
            expect(frame_mode).to_have_value('uniform')
            expect(page.get_by_text('已学完 2 / 3', exact=True)).to_be_hidden()
            learning_nav.click()
            expect(frame_mode).to_have_value('uniform')
            page.go_back()
            expect(workbench_nav).to_have_attribute('aria-current', 'page')
            page.go_forward()
            expect(learning_nav).to_have_attribute('aria-current', 'page')

            # An archived fixture exercises actual media metadata/time and DOM reuse.
            state['with_media'] = True
            page.reload(wait_until='networkidle')
            page.wait_for_function('document.querySelector("video")?.readyState >= 1')
            page.evaluate('''() => {
                window.learningPlayer = document.querySelector('video');
                window.learningNotes = document.querySelector('.learning-notes-panel');
                window.learningPlayer.currentTime = 7;
            }''')
            page.wait_for_function('Math.abs(document.querySelector("video").currentTime - 7) < 0.2')
            workbench_nav.click()
            page.get_by_role('tab', name='视频').click()
            learning_nav.click()
            assert page.evaluate('document.querySelector("video") === window.learningPlayer')
            assert page.evaluate('document.querySelector(".learning-notes-panel") === window.learningNotes')
            assert abs(page.locator('video').evaluate('(video) => video.currentTime') - 7) < 0.2

            if study_test:
                notes_panel = page.locator('.timestamp-notes')
                point_key = (f'{2:032x}', 'video/1080p.wav')
                page.wait_for_function("""async () => {
                    const point = await fetch('/api/workspace/projects/00000000000000000000000000000002/study/progress?resource=video%2F1080p.wav').then(r => r.json());
                    return Math.abs(point.position - 7) < .5;
                }""")
                # Notes actions live only inside Personal notes, not below the player.
                expect(page.locator('.playback-resume p')).to_have_count(0)
                expect(page.locator('.playback-resume').get_by_role('button', name='记下当前时刻', exact=True)).to_have_count(0)
                expect(page.get_by_text('播放位置已保存', exact=True)).to_have_count(0)
                assert abs(state['study'][point_key]['progress']['position'] - 7) < .5
                page.reload(wait_until='networkidle')
                expect(page.get_by_role('button', name='继续上次学习 · 00:07', exact=True)).to_be_visible()
                assert page.locator('video').evaluate('v => v.currentTime') < 1, 'Offer must not autoplay or overwrite the saved point'
                page.get_by_role('button', name='继续上次学习 · 00:07', exact=True).click()
                page.wait_for_function('document.querySelector("video").currentTime >= 7')
                page.locator('video').evaluate('v => v.pause()')
                # Open Personal notes to capture the current timestamp and focus the editor.
                expect(page.get_by_role('tab', name='章节', exact=True)).to_have_attribute('aria-selected', 'true')
                page.get_by_role('tab', name='个人笔记', exact=True).click()
                page.get_by_role('button', name='记下当前时刻', exact=True).click()
                expect(page.get_by_role('tab', name='个人笔记', exact=True)).to_have_attribute('aria-selected', 'true')
                editor = notes_panel.locator('textarea')
                expect(editor).to_be_focused()
                editor.fill('第一条个人笔记：<script>不能执行</script>')
                expect(notes_panel.locator('.timestamp-notes-list')).to_contain_text('第一条个人笔记')
                notes_panel.get_by_role('button', name='关闭编辑', exact=True).click()
                notes_panel.get_by_role('button', name='编辑 00:07 的笔记', exact=True).click()
                editor.fill('编辑后的笔记')
                expect(notes_panel.locator('.timestamp-notes-list')).to_contain_text('编辑后的笔记')
                state['fail_notes'] = True
                editor.fill('保存失败后仍要恢复的草稿')
                expect(notes_panel.get_by_role('alert')).to_contain_text('模拟保存失败')
                # Side tabs and collapse keep the same editor node and failed-save draft.
                page.evaluate("window.studyEditor = document.querySelector('#personal-note-text'); window.studyVideo = document.querySelector('video')")
                width_before = page.locator('#video-stage').bounding_box()['width']
                page.get_by_role('tab', name='章节', exact=True).click()
                expect(notes_panel).to_be_hidden()
                page.get_by_role('tab', name='章节', exact=True).press('End')
                expect(page.get_by_role('tab', name='AI 对话', exact=True)).to_be_focused()
                page.keyboard.press('ArrowLeft')
                expect(page.get_by_role('tab', name='个人笔记', exact=True)).to_be_focused()
                expect(editor).to_have_value('保存失败后仍要恢复的草稿')
                page.get_by_role('button', name='收起学习侧栏', exact=True).click()
                expect(notes_panel).to_be_hidden()
                assert page.locator('#video-stage').bounding_box()['width'] > width_before
                page.get_by_role('button', name='展开学习侧栏', exact=True).click()
                expect(editor).to_have_value('保存失败后仍要恢复的草稿')
                assert page.evaluate("window.studyEditor === document.querySelector('#personal-note-text') && window.studyVideo === document.querySelector('video')")
                page.reload(wait_until='networkidle')
                page.get_by_role('tab', name='个人笔记', exact=True).click()
                expect(notes_panel.locator('textarea')).to_have_value('保存失败后仍要恢复的草稿')
                state['fail_notes'] = False
                notes_panel.get_by_role('button', name='立即保存笔记', exact=True).click()
                expect(notes_panel.locator('.timestamp-notes-list')).to_contain_text('保存失败后仍要恢复的草稿')
                notes_panel.get_by_role('button', name='关闭编辑', exact=True).click()
                page.get_by_role('button', name='从头播放', exact=True).click()
                page.wait_for_function('document.querySelector("video").currentTime < 1')
                page.locator('video').evaluate('v => v.pause()')
                page.locator('video').evaluate('v => {v.currentTime=12; v.dispatchEvent(new Event("timeupdate"));}')
                page.get_by_role('button', name='记下当前时刻', exact=True).click()
                notes_panel.locator('textarea').fill('第二条笔记')
                expect(notes_panel.locator('.timestamp-notes-list li')).to_have_count(2)
                notes_panel.get_by_role('button', name='关闭编辑', exact=True).click()
                notes_panel.get_by_role('button', name='跳到笔记 00:07', exact=True).click()
                page.wait_for_function('document.querySelector("video").currentTime >= 7 && document.querySelector("video").currentTime < 8')
                page.locator('video').evaluate('v => v.pause()')
                page.once('dialog', lambda dialog: dialog.accept())
                notes_panel.get_by_role('button', name='删除 00:12 的笔记', exact=True).click()
                expect(notes_panel.locator('.timestamp-notes-list li')).to_have_count(1)
                for width in (1440,1024,760,390,320,280):
                    page.set_viewport_size({'width':width,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                page.set_viewport_size({'width':1440,'height':1000})
                # Wide screens: the sidebar ends exactly where the bar under the player ends.
                page.wait_for_function("""() => {
                    const bar = document.querySelector('.playback-resume').getBoundingClientRect();
                    const side = document.querySelector('.study-sidebar').getBoundingClientRect();
                    return bar.height > 0 && Math.abs(bar.bottom - side.bottom) <= 1;
                }""")
                out=ROOT/'.local';out.mkdir(exist_ok=True)
                notes_panel.screenshot(path=str(out/'timestamp-notes-20261015.png'))
                open_part(3)
                expect(page.get_by_label('当前这一讲 P3 已学完')).to_be_visible()
                expect(notes_panel.locator('.timestamp-notes-list li')).to_have_count(0)
                expect(page.get_by_role('button', name='继续上次学习 · 00:07', exact=True)).to_have_count(0)
                open_part(2)
                expect(notes_panel.locator('.timestamp-notes-list')).to_contain_text('保存失败后仍要恢复的草稿')
                assert state['learned'] == {1:True,2:True}

            if timeline_test:
                stage = page.locator('#video-stage').bounding_box(); form = page.locator('.learning-materials').bounding_box()
                assert form['y'] >= stage['y'] + stage['height'], 'AI controls must be below video, not beside it'
                assert page.locator('.chapter-timeline').count() == 0
                expect(page.locator('.analysis-form')).to_be_hidden()
                page.get_by_role('button', name='生成/管理 AI 学习材料', exact=True).click()
                expect(page.locator('#video-stage')).to_be_hidden()
                expect(page.get_by_role('complementary', name='学习侧栏', exact=True)).to_be_hidden()
                if page.get_by_role('button', name='生成新材料', exact=True).is_visible():
                    page.get_by_role('button', name='生成新材料', exact=True).click()
                page.get_by_role('button', name='章节时间轴', exact=True).click()
                page.get_by_role('button', name='开始分析', exact=True).click()
                expect(page.get_by_role('button', name='用于当前学习', exact=True)).to_be_enabled()
                page.get_by_role('button', name='用于当前学习', exact=True).click()
                expect(page.locator('.analysis-form')).to_be_hidden()
                expect(page.locator('.analysis-record-actions')).to_be_hidden()
                expect(page.locator('.analysis-ask')).to_be_hidden()
                timeline = page.locator('.chapter-timeline-player')
                directory = page.locator('.chapter-timeline-directory')
                expect(timeline).to_be_visible()
                expect(directory.locator('.chapter-timeline-list li')).to_have_count(3)
                expect(timeline.locator('.chapter-timeline-marker')).to_have_count(3)

                def check_chapter_dots():
                    geometry = timeline.locator('.chapter-timeline-track').evaluate('''track => {
                        const rail = track.getBoundingClientRect();
                        return {lineHeight: parseFloat(getComputedStyle(track, '::before').height),
                            points: [...track.querySelectorAll('.chapter-timeline-marker')].map(button => {
                                const hit = button.getBoundingClientRect();
                                const dot = button.querySelector('.chapter-timeline-dot');
                                const circle = dot.getBoundingClientRect();
                                return {ratio: (circle.x + circle.width / 2 - rail.x) / rail.width,
                                    centerY: circle.y + circle.height / 2 - rail.y - rail.height / 2,
                                    width: circle.width, height: circle.height,
                                    radius: getComputedStyle(dot).borderRadius, text: button.textContent.trim(),
                                    background: getComputedStyle(button).backgroundColor,
                                    border: getComputedStyle(button).borderTopWidth,
                                    hitWidth: hit.width, hitHeight: hit.height};
                            })};
                    }''')
                    assert geometry['lineHeight'] == 2, geometry
                    for point, ratio in zip(geometry['points'], (0, .25, .6)):
                        assert abs(point['ratio'] - ratio) < .005 and abs(point['centerY']) < 1, point
                        assert 10 <= point['width'] <= 14 and point['width'] == point['height'], point
                        assert point['radius'] == '50%' and point['text'] == '', point
                        assert point['background'] == 'rgba(0, 0, 0, 0)' and point['border'] == '0px', point
                        assert point['hitWidth'] >= 32 and point['hitHeight'] >= 32, point

                check_chapter_dots()
                timeline.get_by_role('button', name='跳到 00:05：核心概念', exact=True).hover()
                page.mouse.down()
                check_chapter_dots()  # Global pressed-button styles must not move the dots.
                page.mouse.up()
                timeline.get_by_role('button', name='跳到 00:05：核心概念', exact=True).click()
                page.wait_for_function('document.querySelector("video").currentTime >= 5 && document.querySelector("video").currentTime < 6')
                page.locator('video').evaluate('(video) => video.pause()')
                expect(directory.locator('.chapter-timeline-list button').nth(1)).to_have_attribute('aria-current', 'true')
                intro = timeline.get_by_role('region', name='当前章节介绍', exact=True)
                expect(timeline.locator('.chapter-timeline-description')).to_have_attribute('open', '')
                expect(intro.get_by_role('heading')).to_have_text('核心概念')
                expect(intro.locator('time')).to_have_text('00:05 — 00:12')
                expect(intro).to_contain_text('理解核心概念和使用方法。')
                timeline.locator('.chapter-timeline-marker').nth(2).focus(); page.keyboard.press('Enter')
                page.wait_for_function('document.querySelector("video").currentTime >= 12 && document.querySelector("video").currentTime < 13')
                page.locator('video').evaluate('(video) => video.pause()')
                assert page.evaluate('document.querySelector("video") === window.learningPlayer')
                expect(intro.get_by_role('heading')).to_have_text('总结')
                expect(intro).to_contain_text('暂无详细介绍')
                page.locator('video').evaluate("v => { v.currentTime = 1; v.dispatchEvent(new Event('timeupdate')); }")
                expect(intro.get_by_role('heading')).to_have_text('开场')
                expect(intro).to_contain_text('说明学习目标。')
                page.locator('video').evaluate("v => { v.currentTime = 13; v.dispatchEvent(new Event('timeupdate')); }")
                expect(intro.get_by_role('heading')).to_have_text('总结')
                select = page.get_by_role('combobox', name='选择学习材料', exact=True)
                select.select_option('20261015-202200-abcd0004')
                expect(intro.get_by_role('heading')).to_have_text('新版章节')
                expect(intro).to_contain_text('新记录的介绍。')
                page.reload(wait_until='networkidle')
                expect(select).to_have_value('20261015-202200-abcd0004')
                page.wait_for_function('document.querySelector("video")?.readyState >= 1')
                page.locator('video').evaluate("v => { v.currentTime = 13; v.dispatchEvent(new Event('timeupdate')); }")
                expect(intro).to_contain_text('新记录的介绍。')
                # Sidebar AI chat asks about the chosen learning material; the saved answer seeks the video.
                page.get_by_role('tab', name='AI 对话', exact=True).click()
                chat = page.get_by_role('region', name='AI 对话', exact=True)
                expect(chat.locator('.ai-chat-context')).to_have_count(0)
                chat.get_by_label('向 AI 提问', exact=True).fill('第二部分讲了什么？')
                chat.get_by_role('button', name='提问', exact=True).click()
                expect(chat.locator('.ai-chat-answer')).to_contain_text('模拟回答：核心概念从')
                expect(chat.get_by_label('向 AI 提问', exact=True)).to_have_value('')
                assert page.evaluate("Object.keys(localStorage).filter(k=>k.startsWith('video-parser:chat-drafts:')).every(k=>!Object.values(JSON.parse(localStorage.getItem(k))).includes('第二部分讲了什么？'))")
                assert any(method=='POST' and '/conversations/' in path and path.endswith('/ask-task') for method,path in state['requests'])
                expect(chat.locator('.ai-chat-session-heading')).to_contain_text('核心概念讨论')
                if agent_test:
                    # An unconfigured API must not block local-agent conversations.
                    api_session = conversations[f'{2:032x}'][0]
                    old_messages = list(api_session['messages'])
                    ai['configured']=False; ai['consented']=False; config['ai_configured']=False
                    page.reload(wait_until='networkidle')
                    page.get_by_role('tab',name='AI 对话',exact=True).click()
                    expect(chat.get_by_label('向 AI 提问',exact=True)).to_be_disabled()
                    for engine,name in [('claude','Claude Code'),('codex','Codex'),('opencode','OpenCode')]:
                        chat.get_by_role('combobox',name='引擎与模型',exact=True).select_option(engine+'|'+engine+'-fixture')
                        expect(chat.get_by_label('向 AI 提问',exact=True)).to_be_enabled()
                        chat.get_by_label('向 AI 提问',exact=True).fill('native-'+engine)
                        expect(chat.get_by_role('button',name='提问',exact=True)).to_be_enabled()
                        if engine=='claude':
                            expect(chat.get_by_role('button',name='读取本机模型与命令',exact=True)).to_have_count(0)
                            expect(chat.locator('.agent-manage, .agent-mode')).to_have_count(0)
                        if engine in ('claude','codex'):
                            chat.get_by_role('combobox',name='推理强度',exact=True).select_option('high')
                            command_input = chat.get_by_label('向 AI 提问',exact=True)
                            command_input.fill('/fixture native-' + engine)
                            command_input.press('Home')
                            for _ in range(8): command_input.press('ArrowRight')
                            chat.get_by_role('option').filter(has_text='fixture-skill').click()
                            expect(command_input).to_have_value(('$' if engine=='codex' else '/')+'fixture-skill native-' + engine)
                        expect(chat.locator('.ai-chat-consent')).to_have_count(0)
                        page.locator('video').evaluate('v => { v.pause(); v.currentTime=7; }')
                        chat.get_by_role('button',name='提问',exact=True).click()
                        expect(chat.locator('.ai-chat-session-heading')).to_contain_text(name+' 测试对话')
                        expect(chat.locator('.ai-chat-log > li')).to_have_count(1)
                        assert state['agent_calls'][-1]['target']['engine']==engine
                        assert abs(state['agent_calls'][-1]['position']-7)<.1
                    assert len({item['conversation'] for item in state['agent_calls']})==3
                    assert api_session['messages']==old_messages
                    assert not state['agent_probes'], 'Removed inline refresh must not trigger model discovery'
                    # Keep the selected agent in the material generator, with no API configured.
                    page.get_by_role('button',name='生成/管理 AI 学习材料',exact=True).click()
                    material=page.locator('.analysis-form')
                    material.get_by_role('combobox',name='引擎与模型',exact=True).select_option('codex|codex-fixture')
                    page.get_by_role('button',name='章节时间轴',exact=True).click()
                    expect(material.get_by_role('button',name='开始分析',exact=True)).to_be_disabled()
                    material.locator('label.consent input').check()
                    expect(material.get_by_role('button',name='开始分析',exact=True)).to_be_enabled()
                    # The route asserts this target when generating below in the original fixture.
                    page.get_by_role('button',name='开始分析',exact=True).click()
                    expect(page.get_by_role('combobox',name='选择分析记录',exact=True)).to_have_value('20261016-003000-abcd0005')
                    records[f'{2:032x}']=[row for row in records[f'{2:032x}'] if row['id']!='20261016-003000-abcd0005']
                    state['analysis_calls']=1
                    page.get_by_role('button',name='学习视图',exact=True).click()
                    page.get_by_role('tab',name='AI 对话',exact=True).click()
                    for width in (1440,1280,390,320,280):
                        page.set_viewport_size({'width':width,'height':900})
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
                    page.set_viewport_size({'width':1440,'height':1000})
                    page.wait_for_function("""() => {
                        const bar=document.querySelector('.playback-resume').getBoundingClientRect();
                        const side=document.querySelector('.study-sidebar').getBoundingClientRect();
                        return Math.abs(bar.bottom-side.bottom)<=1;
                    }""")
                    out=ROOT/'.local';out.mkdir(exist_ok=True)
                    page.screenshot(path=str(out/'local-agents-fixture.png'))
                    # Restore the original API session so all pre-existing checks still run unchanged.
                    ai['configured']=True;ai['consented']=True;config['ai_configured']=True
                    page.get_by_role('button',name='选择或新建对话',exact=True).click()
                    page.get_by_role('button',name='核心概念讨论',exact=True).click()
                    page.reload(wait_until='networkidle')
                    page.get_by_role('tab',name='AI 对话',exact=True).click()
                    expect(chat.locator('.ai-chat-session-heading')).to_contain_text('核心概念讨论')

                page.get_by_role('button', name='选择或新建对话', exact=True).click()
                page.get_by_role('button', name='＋ 新建对话', exact=True).click()
                expect(chat.locator('.ai-chat-answer')).to_have_count(0)
                expect(chat.locator('.ai-chat-session-heading strong')).to_have_count(0)
                page.get_by_role('button', name='收起学习侧栏', exact=True).click()
                expect(chat).to_be_hidden()
                expect(page.locator('#learning-sidebar-content')).to_be_hidden()
                page.get_by_role('button', name='选择或新建对话', exact=True).click()
                page.get_by_role('button', name='核心概念讨论', exact=True).click()
                expect(chat).to_be_visible()
                expect(chat.locator('.ai-chat-answer')).to_contain_text('模拟回答：核心概念从')
                expect(chat.locator('.ai-chat-context')).to_have_count(0)
                chat.locator('.ai-chat-answer .md-time').first.click()
                page.wait_for_function('document.querySelector("video").currentTime >= 5 && document.querySelector("video").currentTime < 6')
                page.locator('video').evaluate('(video) => video.pause()')
                page.get_by_role('tab', name='AI 对话', exact=True).click()
                page.locator('#ai-chat-question').fill('尚未发送的追问')
                page.get_by_role('button', name='生成/管理 AI 学习材料', exact=True).click()
                page.get_by_role('button', name='生成新材料', exact=True).click()
                page.locator('#prompt').fill('管理参数草稿，不属于个人笔记')
                expect(page.locator('.independent-conversations')).to_have_count(0)
                page.get_by_role('button', name='学习视图', exact=True).click()
                expect(page.locator('#prompt')).to_be_hidden()
                page.get_by_role('button', name='生成/管理 AI 学习材料', exact=True).click()
                expect(page.locator('#prompt')).to_have_value('管理参数草稿，不属于个人笔记')
                expect(page.locator('#analysis-conversation-question')).to_have_count(0)
                page.get_by_role('button', name='学习视图', exact=True).click()
                page.get_by_role('tab', name='AI 对话', exact=True).click()
                expect(page.locator('#ai-chat-question')).to_have_value('尚未发送的追问')
                # Missing either prerequisite hides the chat input; no implicit generation.
                for missing, button in [(['subtitles'],'生成字幕'), (['chapters','summary'],'准备学习材料')]:
                    state['chat_missing'] = missing
                    page.reload(wait_until='networkidle')
                    page.get_by_role('tab', name='AI 对话', exact=True).click()
                    expect(chat.get_by_role('button', name=button, exact=True)).to_be_visible()
                    expect(chat.locator('textarea')).to_have_count(0)
                    expect(page.get_by_role('button', name='选择或新建对话', exact=True)).to_have_count(0)
                state['chat_missing'] = None
                page.reload(wait_until='networkidle')
                page.get_by_role('tab', name='AI 对话', exact=True).click()
                expect(chat.locator('.ai-chat-answer')).to_contain_text('模拟回答：核心概念从')
                # Background generation never silently replaces the chosen lecture material.
                page.get_by_role('button', name='生成/管理 AI 学习材料', exact=True).click()
                if page.get_by_role('button', name='生成新材料', exact=True).is_visible():
                    page.get_by_role('button', name='生成新材料', exact=True).click()
                page.get_by_role('button', name='章节时间轴', exact=True).click()
                page.get_by_role('button', name='开始分析', exact=True).click()
                expect(page.get_by_role('combobox', name='选择分析记录', exact=True)).to_have_value('20261016-003000-abcd0005')
                page.get_by_role('button', name='学习视图', exact=True).click()
                expect(select).to_have_value('20261015-202200-abcd0004')
                page.get_by_role('button', name='生成/管理 AI 学习材料', exact=True).click()
                page.get_by_role('button', name='用于当前学习', exact=True).click()
                expect(select).to_have_value('20261016-003000-abcd0005')
                page.reload(wait_until='networkidle')
                expect(select).to_have_value('20261016-003000-abcd0005')
                select.select_option('20261015-202200-abcd0004')
                state['fail_materials'] = True
                page.reload(wait_until='networkidle')
                expect(page.get_by_role('alert')).to_contain_text('模拟材料读取失败')
                state['fail_materials'] = False
                page.get_by_role('button', name='重新载入材料', exact=True).click()
                expect(select).to_have_value('20261015-202200-abcd0004')
                page.wait_for_function('document.querySelector("video")?.readyState >= 1')
                page.locator('video').evaluate("v => { v.currentTime = 13; v.dispatchEvent(new Event('timeupdate')); }")
                expect(intro).not_to_contain_text('理解核心概念和使用方法。')
                page.locator('video').evaluate("v => { v.currentTime = 1; v.dispatchEvent(new Event('timeupdate')); }")
                # Before the first chapter the intro stays out of the way.
                expect(intro).to_be_hidden()
                select.select_option('20261015-202400-abcd0002'); expect(timeline).to_be_visible()
                assert select.locator('option[value="20261015-202300-abcd0003"]').count() == 0
                page.get_by_role('button', name='生成/管理 AI 学习材料', exact=True).click()
                page.get_by_role('combobox', name='选择分析记录', exact=True).select_option('20261015-202300-abcd0003')
                expect(page.get_by_role('button', name='用于当前学习', exact=True)).to_be_disabled()
                page.get_by_role('button', name='学习视图', exact=True).click()
                expect(timeline).to_be_visible()  # Current-video chapters are independent of the chosen summary.
                select.select_option('20261015-202500-abcd0001')
                for width in (1440, 1024, 760, 390, 320, 280):
                    page.set_viewport_size({'width': width, 'height': 844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                    check_chapter_dots()
                    stage = page.locator('#video-stage').bounding_box(); form = page.locator('.learning-materials').bounding_box()
                    assert form['y'] >= stage['y'] + stage['height'], width
                    side = page.get_by_role('complementary', name='学习侧栏', exact=True).bounding_box()
                    if width > 1170:
                        assert 320 <= side['width'] <= 360 and side['x'] >= stage['x']+stage['width'], (width, side, stage)
                    else:
                        assert side['y'] >= stage['y']+stage['height'], (width, side, stage)
                        assert form['y'] >= side['y']+side['height'], 'Narrow-screen sidebar must precede long AI analysis'
                    items = directory.locator('.chapter-timeline-list li').all()
                    assert items[1].bounding_box()['y'] > items[0].bounding_box()['y']

                page.set_viewport_size({'width': 1440, 'height': 1000})
                out = Path(os.environ.get('VIDEO_PARSER_TEST_OUTPUT', ROOT / '.local')); out.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out / 'chapter-intro-20261015.png'), full_page=True)
                timeline.screenshot(path=str(out / 'chapter-intro-detail-20261015.png'))
                page.locator('.theme-toggle').click()
                check_chapter_dots()
                timeline.screenshot(path=str(out / 'chapter-intro-dark-20261015.png'))
                page.locator('.theme-toggle').click()
                # Long directories have one scroll owner at every breakpoint, and follow playback without moving the page.
                original = next(item for item in records[f'{2:032x}'] if item['id']=='20261015-202500-abcd0001')['text']
                next(item for item in records[f'{2:032x}'] if item['id']=='20261015-202500-abcd0001')['text'] = '\n'.join(f'[00:{i:02d}] 第{i}章' for i in range(20))
                page.reload(wait_until='networkidle')
                expect(directory.locator('.chapter-timeline-list li')).to_have_count(20)
                for width in (1440, 1024, 760, 390, 320, 280):
                    page.set_viewport_size({'width': width, 'height': 844})
                    ownership = directory.evaluate('''node => {
                        const body = node.closest('.study-sidebar-body');
                        const list = node.querySelector('.chapter-timeline-list');
                        const scrolls = element => ['auto', 'scroll'].includes(getComputedStyle(element).overflowY)
                            && element.scrollHeight > element.clientHeight + 1;
                        return { body: scrolls(body), list: scrolls(list), overflow: getComputedStyle(list).overflowY };
                    }''')
                    assert ownership == {'body': False, 'list': True, 'overflow': 'auto'}, (width, ownership)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.wait_for_function("Math.abs(document.querySelector('.playback-resume').getBoundingClientRect().bottom - document.querySelector('.study-sidebar').getBoundingClientRect().bottom) <= 1")
                page.evaluate("window.scrollTo(0,0);const v=document.querySelector('video');v.pause();v.currentTime=18.4;v.dispatchEvent(new Event('timeupdate'))")
                bounds = page.wait_for_function('''() => {
                    const active = document.querySelector('.chapter-timeline-list [aria-current]');
                    if (!active?.textContent.includes('第18章')) return false;
                    const item = active.getBoundingClientRect();
                    const body = document.querySelector('.chapter-timeline-list').getBoundingClientRect();
                    return item.top >= body.top - 1 && item.bottom <= body.bottom + 1
                        ? {item:item.toJSON(),body:body.toJSON(),scrollY} : false;
                }''').json_value()
                item, listing = bounds['item'], bounds['body']
                assert item['y'] >= listing['y']-1 and item['y']+item['height'] <= listing['y']+listing['height']+1
                assert bounds['scrollY'] == 0
                directory.locator('.chapter-timeline-list button').first.click()
                page.wait_for_function('document.querySelector("video").currentTime < 1')
                page.locator('video').evaluate('v => v.pause()')
                next(item for item in records[f'{2:032x}'] if item['id']=='20261015-202500-abcd0001')['text'] = original
                page.reload(wait_until='networkidle')
                workbench_nav.click(); page.get_by_role('tab', name='视频').click()
                expect(timeline).to_be_hidden()
                learning_nav.click()
                open_part(3)
                expect(page.get_by_label('当前这一讲 P3 已学完')).to_be_visible()
                expect(timeline).to_have_count(0)

            page.set_viewport_size({'width': 390, 'height': 844})
            expect(page.get_by_role('button', name='展开分集列表', exact=True)).to_be_visible()
            expect(page.locator('.learning-episode-list')).to_be_hidden()
            page.get_by_role('button', name='展开分集列表', exact=True).click()
            expect(page.get_by_label('标记 P3「总结」已学完')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Learning view overflows on mobile'

            # The same sidebar entry works in the mobile drawer and returns focus to the heading.
            page.get_by_role('button', name='打开导航菜单', exact=True).click()
            expect(learning_nav).to_be_focused()
            page.locator('.app-sidebar').get_by_role('button', name='任务中心', exact=True).click()
            expect(page.get_by_role('heading', name='任务中心', exact=True)).to_be_focused()
            page.get_by_role('button', name='打开导航菜单', exact=True).click()
            learning_nav.click()
            expect(page.get_by_role('heading', name='学习视图', exact=True)).to_be_focused()
            expect(page.locator('.app-sidebar')).not_to_have_class('app-sidebar open')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

            assert state['parse_calls'] == (4 if study_test else 3 if timeline_test else 2), state
            assert state['learned'] == {1: True, 2: True}, state
            assert not any(path.endswith(('/prepare', '/prepare-task')) for _, path in state['requests']), 'Learning controls must not start downloads'
            assert not errors, errors
            print(json.dumps({'checks': [
                'desktop sidebar entry, active state, empty state and unknown-route fallback',
                'learning deep link, project resume and browser back/forward navigation',
                'preparation/management/reader roles are distinct; management drafts, player DOM and time survive page switches',
                'mobile drawer entry closes the menu and focuses the learning heading',
                'progress and learned status synchronize between the episode list and player',
                'continue learning opens the next unlearned episode and preserves cross-part state',
                'mobile episode list expands without horizontal overflow',
                'no approval flow or implicit archive/download request',
                *(['three grouped agent selectors; no API-key gate; consent/effort/Skills; engine changes create isolated sessions; native material generation; 1440–280px layout'] if agent_test else []),
                *(['precise resume, note CRUD/seek/recovery/isolation, sidebar keyboard tabs/collapse retain editor and player, 1440–280px layout'] if study_test else []),
                *(['reader without generation/management noise, explicit use material, reload preference, foreign-resource guard, directory/intro/seek, sidebar AI chat ask/answer/seek and responsive order'] if timeline_test else []),
            ], 'api_requests': state['requests']}, ensure_ascii=False, indent=2))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
