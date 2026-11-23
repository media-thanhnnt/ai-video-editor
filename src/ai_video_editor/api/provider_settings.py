"""Editable local provider/model catalog; API credentials stay exclusively in OS keyring.

Custom providers use the OpenAI Responses API wire format. A model name being
listed does not guarantee that the remote model supports structured responses,
image generation or speech generation; the user must verify vendor compatibility.
"""
from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

from ..project import ValidationError, atomic_write_json, read_json

PROVIDERS = {
    'openai': {'label': 'OpenAI', 'base_url': 'https://api.openai.com/v1', 'env': 'OPENAI_API_KEY', 'adapter': 'openai'},
    'gemini': {'label': 'Google Gemini', 'base_url': 'https://generativelanguage.googleapis.com/v1beta', 'env': 'GEMINI_API_KEY', 'adapter': 'gemini'},
}
CATALOG = {
    'script': {'openai': ['gpt-6-luna', 'gpt-4.1-mini'], 'gemini': ['gemini-2.5-flash']},
    'scene_plan': {'openai': ['gpt-6-luna', 'gpt-4.1-mini'], 'gemini': ['gemini-2.5-flash']},
    'image': {'openai': ['gpt-image-2', 'gpt-image-1.5'], 'gemini': ['gemini-2.5-flash-image']},
    'audio': {'openai': ['gpt-4o-mini-tts', 'tts-1'], 'gemini': ['gemini-2.5-flash-preview-tts']},
    'video': {},
}
DEFAULTS = {task: (next(iter(options)), next(iter(options.values()))[0]) for task, options in CATALOG.items() if options}
LOCK = RLock()
SERVICE = 'ai-video-editor-local'
ID = re.compile(r'^[a-z][a-z0-9_-]{1,39}$')
MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,119}$')
TASKS = ('script', 'scene_plan', 'image', 'audio')


def catalog_path(projects_dir):
    return Path(projects_dir).parent / 'config' / 'providers.json'


def settings_path(projects_dir):
    return Path(projects_dir).parent / '.ai_video_editor_provider_settings.json'


def _safe_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 300:
        raise ValidationError('Provider base URL must be a short HTTPS URL')
    try:
        u = urlsplit(value)
        host = u.hostname or ''
        if (u.scheme != 'https' or not host or u.username or u.password or
                u.query or u.fragment or u.port not in (None, 443) or not host.isascii() or
                host == 'localhost' or host.endswith(('.local', '.internal')) or '.' not in host):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
    except ValueError as exc:
        raise ValidationError('Provider URL must be an HTTPS hostname (not localhost, an IP, or a URL with credentials)') from exc
    return value.rstrip('/')


def _validate_catalog(data):
    if not isinstance(data, dict) or set(data) != {'schema_version', 'providers', 'models'} or data['schema_version'] != 1:
        raise ValidationError('Invalid providers.json schema')
    providers, catalog = data['providers'], data['models']
    if not isinstance(providers, dict) or not isinstance(catalog, dict) or len(providers) > 30:
        raise ValidationError('Invalid provider/model registry')
    if not set(PROVIDERS).issubset(providers):
        raise ValidationError('Built-in providers cannot be deleted')
    cleaned = {}
    for pid, raw in providers.items():
        if not isinstance(pid, str) or not ID.fullmatch(pid) or not isinstance(raw, dict):
            raise ValidationError('Invalid provider ID or definition')
        if pid in PROVIDERS:
            # Do not permit overriding official endpoint or adapter via editable config.
            cleaned[pid] = dict(PROVIDERS[pid])
        else:
            label, url = raw.get('label'), raw.get('base_url')
            if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
                raise ValidationError('Invalid provider display name')
            if raw.get('adapter') != 'openai_compatible':
                raise ValidationError('Custom providers must use the openai_compatible adapter')
            cleaned[pid] = {'label': label.strip(), 'base_url': _safe_url(url),
                            'adapter': 'openai_compatible', 'env': ''}
    result = {task: {} for task in CATALOG}
    for task, entries in catalog.items():
        if task not in result or not isinstance(entries, dict):
            raise ValidationError('Invalid task in catalog')
        for pid, models in entries.items():
            if pid not in cleaned or not isinstance(models, list) or len(models) > 100:
                raise ValidationError('Invalid provider models')
            if task == 'video' and models:
                raise ValidationError('Video generation adapter is not implemented')
            if len(models) != len(set(models)) or any(not isinstance(m, str) or not MODEL.fullmatch(m) for m in models):
                raise ValidationError('Invalid or duplicate model name')
            if models:
                result[task][pid] = list(models)
    if not all(result[t] for t in TASKS):
        raise ValidationError('Every required task must have at least one model')
    return {'schema_version': 1, 'providers': cleaned, 'models': result}


def load_registry(projects_dir):
    with LOCK:
        path = catalog_path(projects_dir)
        default = {'schema_version': 1, 'providers': PROVIDERS, 'models': CATALOG}
        return _validate_catalog(read_json(path) if path.exists() else default)


def _write_registry(projects_dir, raw):
    catalog = _validate_catalog(raw)
    path = catalog_path(projects_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, catalog)
    return catalog


def add_provider(projects_dir, *, provider_id, label, base_url, adapter='openai_compatible'):
    with LOCK:
        catalog = load_registry(projects_dir)
        if not isinstance(provider_id, str) or not ID.fullmatch(provider_id):
            raise ValidationError('Provider ID must be 2–40 lowercase letters, digits, - or _')
        if provider_id in catalog['providers']:
            raise ValidationError('Provider already exists')
        catalog['providers'][provider_id] = {'label': label, 'base_url': base_url, 'adapter': adapter, 'env': ''}
        return _write_registry(projects_dir, catalog)


def add_model(projects_dir, *, provider_id, task, model):
    with LOCK:
        catalog = load_registry(projects_dir)
        if task not in TASKS or provider_id not in catalog['providers']:
            raise ValidationError('Unknown task/provider')
        if not isinstance(model, str) or not MODEL.fullmatch(model):
            raise ValidationError('Invalid model name')
        values = catalog['models'][task].setdefault(provider_id, [])
        if model in values:
            raise ValidationError('Model already exists for this task')
        values.append(model)
        return _write_registry(projects_dir, catalog)


def delete_model(projects_dir, *, provider_id, task, model):
    with LOCK:
        catalog = load_registry(projects_dir)
        values = catalog['models'].get(task, {}).get(provider_id, [])
        if model not in values:
            raise ValidationError('Model does not exist')
        if len(values) == 1 and len(catalog['models'][task]) == 1:
            raise ValidationError('Cannot delete last supported model for a task')
        values.remove(model)
        if not values:
            del catalog['models'][task][provider_id]
        result = _write_registry(projects_dir, catalog)
        # Repair stale selection safely at next load.
        return result


def delete_provider(projects_dir, provider_id):
    with LOCK:
        catalog = load_registry(projects_dir)
        if provider_id in PROVIDERS:
            raise ValidationError('Cannot delete built-in provider')
        if provider_id not in catalog['providers']:
            raise ValidationError('Unknown provider')
        del catalog['providers'][provider_id]
        for task in catalog['models'].values():
            task.pop(provider_id, None)
        # Clear credentials BEFORE deleting the provider. If the keyring fails,
        # keep the provider record so the ID cannot be reused with its old key.
        try:
            store = keyring_lib()
            if store.get_password(SERVICE, provider_id):
                store.delete_password(SERVICE, provider_id)
        except Exception as exc:
            raise ValidationError('Could not remove provider credential; provider was not deleted') from exc
        return _write_registry(projects_dir, catalog)


def keyring_lib():
    try:
        import keyring
        return keyring
    except ImportError as exc:
        raise ValidationError('Install UI dependencies with pip install -e "[ui]"') from exc


def _known(provider, projects_dir=None):
    providers = load_registry(projects_dir)['providers'] if projects_dir is not None else PROVIDERS
    if provider not in providers:
        raise ValidationError('Unknown provider')
    return providers[provider]


def get_key(provider, projects_dir=None):
    info = _known(provider, projects_dir)
    try:
        key = keyring_lib().get_password(SERVICE, provider)
        if key:
            return key
    except Exception:
        pass
    return os.environ.get(info['env']) or None if info.get('env') else None


def save_key(provider, key, projects_dir=None):
    _known(provider, projects_dir)
    if not isinstance(key, str) or not 8 <= len(key) <= 512 or '\n' in key or '\r' in key:
        raise ValidationError('Invalid key')
    try:
        keyring_lib().set_password(SERVICE, provider, key.strip())
    except Exception as exc:
        raise ValidationError('OS credential store unavailable; key NOT saved') from exc


def delete_key(provider, projects_dir=None):
    _known(provider, projects_dir)
    try:
        keyring_lib().delete_password(SERVICE, provider)
    except Exception as exc:
        raise ValidationError('No stored key or credential store unavailable') from exc


def load_choices(projects_dir):
    with LOCK:
        catalog = load_registry(projects_dir)['models']
        path = settings_path(projects_dir)
        raw = read_json(path) if path.exists() else {}
        result = {}
        for task, options in catalog.items():
            if not options:
                continue
            default = DEFAULTS.get(task)
            if not default or default[0] not in options or default[1] not in options[default[0]]:
                default = (next(iter(options)), next(iter(options.values()))[0])
            s = raw.get(task, {}) if isinstance(raw, dict) else {}
            if not isinstance(s, dict):
                s = {}
            provider, model = s.get('provider', default[0]), s.get('model', default[1])
            if provider not in options or model not in options[provider]:
                provider, model = default
            result[task] = {'provider': provider, 'model': model}
        return result


def save_choices(projects_dir, choices):
    with LOCK:
        result = load_choices(projects_dir)
        catalog = load_registry(projects_dir)['models']
        for task, choice in choices.items():
            if task not in result or not isinstance(choice, dict):
                raise ValidationError('Invalid task')
            provider, model = choice.get('provider'), choice.get('model')
            if provider not in catalog[task] or model not in catalog[task][provider]:
                raise ValidationError('Unsupported provider/model for ' + task)
            result[task] = {'provider': provider, 'model': model}
        atomic_write_json(settings_path(projects_dir), result)
        return result


def provider_info(projects_dir, provider):
    return _known(provider, projects_dir)


def public_settings(projects_dir):
    registry = load_registry(projects_dir)
    # Never return env variable contents or API keys.
    return {'providers': {name: {**info, 'has_key': bool(get_key(name, projects_dir))}
                          for name, info in registry['providers'].items()},
            'catalog': registry['models'], 'choices': load_choices(projects_dir),
            'catalog_file': str(catalog_path(projects_dir))}
