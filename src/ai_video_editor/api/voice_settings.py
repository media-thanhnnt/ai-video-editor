"""Per-project TTS voice preferences. No credentials or paid API operations.

The selected voice is validated for the active audio provider before starting a
billable job. This prevents a misleading UI dropdown that the backend ignores.
"""
from __future__ import annotations

from ..project import ValidationError, atomic_write_json, read_json

OPENAI_VOICES = ('alloy', 'ash', 'ballad', 'coral', 'echo', 'fable', 'onyx', 'nova', 'sage', 'shimmer')
GEMINI_VOICES = ('Kore', 'Puck', 'Charon', 'Fenrir', 'Aoede', 'Zephyr')
FILENAME = 'voice_preferences.json'


def options(provider: str, adapter: str) -> list[str]:
    if provider == 'gemini' or adapter == 'gemini':
        return list(GEMINI_VOICES)
    return list(OPENAI_VOICES)


def default_voice(provider: str, adapter: str) -> str:
    return 'Kore' if provider == 'gemini' or adapter == 'gemini' else 'alloy'


def validate_voice(voice: str, provider: str, adapter: str) -> str:
    if not isinstance(voice, str) or voice not in options(provider, adapter):
        raise ValidationError(f'Voice {voice!r} is not supported by audio provider {provider}; choose a listed voice')
    return voice


def load(project, provider: str, adapter: str) -> str:
    path = project.folder / FILENAME
    if path.exists():
        raw = read_json(path)
        if not isinstance(raw, dict) or raw.get('schema_version') != 1 or not isinstance(raw.get('voices'), dict):
            raise ValidationError('Invalid voice_preferences.json')
        stored = raw['voices'].get(provider)
        if stored is not None:
            return validate_voice(stored, provider, adapter)
    return default_voice(provider, adapter)


def save(project, provider: str, adapter: str, voice: str) -> str:
    value = validate_voice(voice, provider, adapter)
    path = project.folder / FILENAME
    raw = read_json(path) if path.exists() else {'schema_version': 1, 'voices': {}}
    if not isinstance(raw, dict) or raw.get('schema_version') != 1 or not isinstance(raw.get('voices'), dict):
        raise ValidationError('Invalid voice_preferences.json')
    raw['voices'][provider] = value
    atomic_write_json(path, raw)
    return value
