"""Device-local agent switches, separate from browser model visibility preferences."""
import json
from threading import RLock
from video_parser.core.paths import ROOT
from video_parser.services import task_runtime

PATH = ROOT / '.local/ai/agent-controls.json'
ENGINES = ('claude', 'codex', 'opencode', 'dsh')
LOCK = RLock()

def current():
    with LOCK:
        if not PATH.exists():
            return dict.fromkeys(ENGINES, True)
        try:
            data = json.loads(PATH.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError()
            return {name: data.get(name, True) is True for name in ENGINES}
        except (ValueError, OSError):
            # Invalid persisted controls must not silently reactivate disabled agents.
            return dict.fromkeys(ENGINES, False)

def enabled(engine):
    return engine == 'api' or current().get(engine, False)

def save(engine, value):
    if engine not in ENGINES or not isinstance(value, bool):
        raise ValueError('无效 Agent 开关。')
    with LOCK:
        data = current()
        data[engine] = value
        task_runtime.atomic(PATH, data)
    return data
