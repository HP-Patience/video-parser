"""Read-only filename labels for tasks, including legacy journals and physical renames."""
from pathlib import PureWindowsPath
from video_parser.services import library, resource_renames

MEDIA = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus'}

FORMATS = {'.srt': 0, '.vtt': 1, '.mp4': 2, '.mkv': 3, '.wav': 4, '.txt': 5, '.md': 6, '.json': 7}


def filename_labels():
    projects, plans = {}, {}

    def project(identifier):
        if not identifier: return {}
        if identifier not in projects:
            try: projects[identifier] = library.get(identifier)
            except (KeyError, ValueError, OSError, TypeError): projects[identifier] = {}
        return projects[identifier]

    def basename(value):
        return PureWindowsPath(str(value)).name if value else ''

    def resource(owner, key, fallback=''):
        info = project(owner).get('resources', {}).get(key) if key else None
        # Logical keys stay stable after naming; the physical storage_path is the current filename.
        return basename(info.get('storage_path') or key) if info else basename(fallback or key)

    def plan(identifier):
        if not identifier: return {}
        if identifier not in plans:
            try: plans[identifier] = resource_renames.load_plan(identifier)
            except (KeyError, ValueError, OSError, TypeError): plans[identifier] = {}
        return plans[identifier]

    def video_sources(job, category='', seen=None, with_refs=False):
        seen = set() if seen is None else seen
        identity = getattr(job, 'id', id(job))
        if identity in seen or len(seen) >= 8: return []
        seen.add(identity)
        found = {}

        def add(owner, key=None, infer=False):
            resources = project(owner).get('resources', {})
            if key:
                info = resources.get(key)
                if info and info.get('kind') != 'video':
                    key = info.get('video_resource')
                    if not key: return
                    info = resources.get(key)
                if (info and info.get('kind') == 'video') or (not info and PureWindowsPath(key or '').suffix.lower() in MEDIA):
                    found[(owner, key)] = resource(owner, key)
            elif infer:
                videos = [name for name, info in resources.items() if info.get('kind') == 'video']
                if len(videos) == 1: add(owner, videos[0])

        owner = getattr(job, 'project_id', None)
        kind = getattr(job, 'kind', '') or category
        operation = getattr(job, 'operation', '')
        result = getattr(job, 'result', {}) or {}
        payload = getattr(job, 'payload', {}) or {}
        if kind == 'naming':
            record = plan(result.get('plan_id') or payload.get('plan_id'))
            moves = (record.get('preview') or {}).get('moves', [])
            if moves:
                for move in moves: add(move['project_id'], move['resource'])
            else:
                for entry in record.get('entries', []):
                    for file in entry.get('files', []): add(entry['project_id'], file['resource'])
        elif kind == 'subtitles':
            for row in getattr(job, 'results', []):
                pid = (row.get('project') or {}).get('id')
                prefix = f'/api/workspace/projects/{pid}/files/'
                for file in row.get('files', []):
                    url = file.get('url', '').split('?', 1)[0]
                    if url.startswith(prefix): add(pid, url[len(prefix):])
                if not any(pair[0] == pid for pair in found): add(pid, infer=True)
        elif kind == 'transcriptions' or operation in ('transcription', 'translation'):
            key = getattr(job, 'resource', None)
            if key: add(owner, key)
            ref = (getattr(job, 'translation', {}) or {}).get('reference', {})
            if not found and (ref.get('video_resource') or ref.get('resource')):
                add(ref.get('project_id') or owner, ref.get('video_resource') or ref.get('resource'))
            if not found:
                for item in (getattr(job, 'files', {}) or {}).values():
                    if isinstance(item, dict): add(item.get('project_id') or owner, item.get('relative'))
            if not found and ref.get('job_id'):
                from video_parser.services import transcription
                try: return video_sources(transcription.get(ref['job_id']), 'transcriptions', seen, with_refs)
                except (KeyError, ValueError, OSError, TypeError): pass
            if not found and not key: add(owner or ref.get('project_id'), infer=True)
            if not found and operation != 'translation' and not owner:
                name = basename(getattr(job, 'title', '') if getattr(job, 'owned_input', False) else getattr(job, 'source', ''))
                if PureWindowsPath(name).suffix.lower() in MEDIA: found[(None, name)] = name
        else:
            record = result.get('record') or {}
            key = result.get('resource') if kind == 'video' else record.get('video_resource') or payload.get('resource') or payload.get('video_resource')
            add(owner, key, infer=not key)
        return list(found.items()) if with_refs else list(found.values())

    def presentation(job, category):
        kind = getattr(job, 'kind', '') or category
        operation = getattr(job, 'operation', '')
        owner = getattr(job, 'project_id', None)
        subjects, files = {}, {}

        def subject(pid, part=None):
            data = project(pid)
            part = data.get('part') or part
            key = pid or ('part', part)
            title = library.episode_title(data) or ''
            # A single-video platform's title is not a series title. Never use a course title for a missing part.
            # A one-part video has no episode title at all, so its own title is the only useful label.
            single = part in (None, 1) and len(library.normalize_episodes(data.get('episodes'))) <= 1
            if not title and data and single:
                title = data.get('title') or ''
            subjects[key] = {'project_id': pid, 'part': part, 'title': title}

        def file(pid, key, role=None, fallback='', part=None):
            info = project(pid).get('resources', {}).get(key, {}) if key else {}
            name = resource(pid, key, fallback)
            if not name: return
            resource_kind = info.get('kind')
            role = role or {'video': 'video', 'subtitle': 'subtitle', 'transcription': 'transcription', 'translation': 'translation', 'correction': 'subtitle'}.get(resource_kind, 'file')
            if role == 'video' and PureWindowsPath(name).suffix.lower() in {'.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus'}: role = 'audio'
            files[(pid, key or str(fallback), role, part)] = {'project_id': pid, 'part': project(pid).get('part') or part, 'role': role, 'name': name}

        video_refs = video_sources(job, category, with_refs=True)
        for (pid, key), name in video_refs:
            file(pid, key, 'video', name)

        if kind == 'naming':
            record = plan((getattr(job, 'result', {}) or {}).get('plan_id') or (getattr(job, 'payload', {}) or {}).get('plan_id'))
            preview = record.get('preview') or {}
            selected = {row['project_id'] for row in preview.get('rows', [])} if preview else None
            for entry in record.get('entries', []):
                pid = entry['project_id']
                if selected is not None and pid not in selected: continue
                subject(pid, entry.get('part'))
                for item in resource_renames.plan_files(record, entry): file(pid, item['resource'], fallback=item.get('filename', ''))
            # Older journals may only retain preview moves.
            for move in preview.get('moves', []):
                if move['project_id'] not in subjects: subject(move['project_id'])
                file(move['project_id'], move['resource'], fallback=move.get('source', ''))
        elif kind == 'subtitles':
            for row in getattr(job, 'results', []):
                pid = (row.get('project') or {}).get('id'); part = row.get('episode')
                subject(pid, part)
                prefix = f'/api/workspace/projects/{pid}/files/'
                for item in row.get('files', []):
                    url = item.get('url', '').split('?', 1)[0]
                    file(pid, url[len(prefix):] if url.startswith(prefix) else None, 'subtitle', item.get('name', ''), part)
            for part in getattr(job, 'episodes', None) or []:
                if not any(value['part'] == part for value in subjects.values()): subject(None, part)
        else:
            ref = (getattr(job, 'translation', {}) or {}).get('reference', {})
            if owner or ref.get('project_id'): subject(owner or ref.get('project_id'))
            elif video_refs:
                for (pid, key), name in video_refs:
                    if pid: subject(pid)
            if kind == 'transcriptions' or operation in ('transcription', 'translation'):
                if operation == 'translation':
                    if ref.get('resource'):
                        file(ref.get('project_id') or owner, ref['resource'], 'source_subtitle')
                    elif ref.get('job_id'):
                        from video_parser.services import transcription
                        try:
                            parent = transcription.get(ref['job_id'])
                            item = parent.files.get('srt') or parent.files.get('txt') or {}
                            file(item.get('project_id'), item.get('relative'), 'source_subtitle', item.get('path', ''))
                        except (KeyError, ValueError, OSError, TypeError): pass
                    elif getattr(job, 'source', None): file(None, None, 'source_subtitle', job.source)
                for item in (getattr(job, 'files', {}) or {}).values():
                    if isinstance(item, dict): file(item.get('project_id') or owner, item.get('relative'), 'translation' if operation == 'translation' else 'transcription', item.get('path', ''))

        if not files:
            for index, name in enumerate(resolve(job, category)):
                files[('legacy', index)] = {'project_id': owner, 'part': project(owner).get('part'), 'role': 'file', 'name': name}
        # Standalone inputs have a real filename, not a project/episode title.
        if not subjects and kind not in ('naming', 'subtitles') and not owner:
            names = [name for _, name in video_refs] or resolve(job, category)
            if names: subjects['standalone'] = {'project_id': None, 'part': None, 'title': names[0]}
        ordered = sorted(subjects.values(), key=lambda item: (item['part'] is None, item['part'] or 0, item['project_id'] or ''))
        # Dedupe registered files by identity and role; do not merge same basenames across episodes.
        unique = {}
        for identity, item in files.items():
            unique[(identity[0], identity[1], item['role'], item['part'])] = item
        return {'subjects': ordered, 'file_details': list(unique.values())}

    def resolve(job, category='', *, video_only=False, with_presentation=False):
        if with_presentation: return presentation(job, category)
        if video_only: return video_sources(job, category)
        owner = getattr(job, 'project_id', None)
        kind = getattr(job, 'kind', '') or category
        operation = getattr(job, 'operation', '')
        result = getattr(job, 'result', {}) or {}
        payload = getattr(job, 'payload', {}) or {}
        names = []
        if kind == 'naming':
            record = plan(result.get('plan_id') or payload.get('plan_id'))
            moves = (record.get('preview') or {}).get('moves', [])
            if moves:
                names = [resource(move['project_id'], move['resource'], move.get('source')) for move in moves]
            else:
                names = [resource(entry['project_id'], file['resource'], file.get('source') or file.get('filename'))
                         for entry in record.get('entries', []) for file in entry.get('files', [])]
        elif kind == 'subtitles':
            # Published per-episode records have owner/resource links, unlike the old course-level title.
            for row in getattr(job, 'results', []):
                pid = (row.get('project') or {}).get('id')
                for file in row.get('files', []):
                    url = file.get('url', '').split('?', 1)[0]
                    prefix = f'/api/workspace/projects/{pid}/files/'
                    key = url[len(prefix):] if url.startswith(prefix) else None
                    names.append(resource(pid, key, file.get('name')))
            if not names:
                names = [basename(path) for path in getattr(job, 'files', {}).values() if isinstance(path, (str, PureWindowsPath)) or hasattr(path, 'name')]
        elif kind == 'transcriptions' or operation in ('transcription', 'translation'):
            files = getattr(job, 'files', {}) or {}
            names = [resource(item.get('project_id') or owner, item.get('relative'), item.get('path')) for item in files.values() if isinstance(item, dict)]
            if not names and operation == 'translation':
                ref = (getattr(job, 'translation', {}) or {}).get('reference', {})
                if ref.get('resource'): names = [resource(ref.get('project_id') or owner, ref['resource'])]
                elif getattr(job, 'source', None): names = [basename(job.source)]
            if not names:
                if owner and getattr(job, 'resource', None): names = [resource(owner, job.resource)]
                elif getattr(job, 'owned_input', False): names = [basename(getattr(job, 'title', ''))]
                elif getattr(job, 'source', None): names = [basename(job.source)]
        else:
            record = result.get('record') or {}
            key = result.get('resource') if kind == 'video' else record.get('video_resource') or payload.get('resource') or payload.get('video_resource')
            if key: names = [resource(owner, key, result.get('filename'))]
            elif result.get('filename'): names = [basename(result['filename'])]
        if not names and kind in ('video', 'analysis'):
            videos = [key for key, info in project(owner).get('resources', {}).items() if info.get('kind') == 'video']
            # A legacy row with no source link is only inferable when its project has exactly one video.
            if len(videos) == 1: names = [resource(owner, videos[0])]
        if not names:
            title = basename(getattr(job, 'title', ''))
            if PureWindowsPath(title).suffix.lower() in {*FORMATS, '.webm', '.mov', '.avi', '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus'}:
                names = [title]
        return sorted((name for name in names if name), key=lambda name: FORMATS.get(PureWindowsPath(name).suffix.lower(), 10))

    return resolve
