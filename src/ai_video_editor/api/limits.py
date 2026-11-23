"""Local per-project rate budget, not the OpenAI provider account quota."""
from datetime import datetime, timezone
from threading import RLock
from ..project import atomic_write_json, read_json

DEFAULT_LIMITS = {'image': 5, 'audio': 10}
_LOCK = RLock()


def _limits(project):
    path = project.folder / 'api_request_limits.json'
    if not path.exists():
        return dict(DEFAULT_LIMITS)
    raw = read_json(path)
    result = raw.get('limits', {})
    if not isinstance(result, dict) or any(type(result.get(k)) is not int or
                                            result[k] < 0 for k in DEFAULT_LIMITS):
        raise ValueError('Invalid local limits configuration')
    return {k: result[k] for k in DEFAULT_LIMITS}


def set_limits(project, *, image: int, audio: int):
    for kind, number in [('image', image), ('audio', audio)]:
        if type(number) is not int or number < 0:
            raise ValueError(f'{kind} daily cap must be a non-negative integer')
    with _LOCK:
        atomic_write_json(project.folder / 'api_request_limits.json',
                          {'schema_version': '2.4.5', 'limits': {'image': image, 'audio': audio}})
        return usage(project)


def usage(project):
    today = datetime.now(timezone.utc).date().isoformat()
    path = project.folder / 'api_request_usage.json'
    with _LOCK:
        try:
            previous = read_json(path) if path.is_file() else {}
        except (OSError, ValueError):
            previous = {}
        used = previous.get('used', {}) if previous.get('date_utc') == today else {}
        state = {'date_utc': today, 'used': {k: max(0, int(used.get(k, 0))) for k in DEFAULT_LIMITS},
                 'limits': _limits(project)}
        state['remaining'] = {k: max(0, state['limits'][k] - state['used'][k]) for k in DEFAULT_LIMITS}
        return state


def reserve(project, kind):
    """Reserve an attempt before calling OpenAI. Failed requests still count as attempts."""
    with _LOCK:
        state = usage(project)
        if kind not in DEFAULT_LIMITS:
            raise ValueError('Only image/audio count toward local API limits')
        if state['used'][kind] >= state['limits'][kind]:
            raise ValueError(f"Local {kind} daily cap reached ({state['limits'][kind]} attempts, UTC).")
        state['used'][kind] += 1
        atomic_write_json(project.folder / 'api_request_usage.json',
                          {'date_utc': state['date_utc'], 'used': state['used']})
        return usage(project)
