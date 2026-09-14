"""Strict, masked access to the original global desktop configuration."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from config_manager import get_default_config

SECRET_KEYS = {'api_key', 'password', 'webdav_password', 'token', 'secret', 'access_token'}

class ConfigConflict(ValueError):
    pass

def digest(raw):
    return hashlib.sha256(raw or b'').hexdigest()

def atomic_write(path, data, expected):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.web-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        current = path.read_bytes() if path.exists() else None
        if current != expected:
            raise ConfigConflict('文件已更新，请重新加载')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def _walk(obj, path=()):
    if isinstance(obj, dict):
        for key, value in obj.items():
            current = path + (key,)
            if key.lower() in SECRET_KEYS:
                yield current
            else:
                yield from _walk(value, current)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _walk(value, path + (index,))

def _get(obj, path):
    try:
        for part in path:
            obj = obj[part]
        return obj
    except (TypeError, KeyError, IndexError):
        return None

def _set(obj, path, value):
    try:
        for key in path[:-1]:
            obj = obj[key]
        obj[path[-1]] = value
    except (TypeError, KeyError, IndexError) as exc:
        raise ValueError('密钥字段路径无效') from exc

def validate(config):
    if not isinstance(config, dict):
        raise ValueError('配置必须是对象')
    defaults = get_default_config()
    for section in ('llm_configs', 'embedding_configs', 'other_params', 'choose_configs'):
        if section not in config or not isinstance(config[section], dict):
            raise ValueError('配置缺少有效对象：' + section)
    for section in ('proxy_setting', 'webdav_config'):
        if section in config and not isinstance(config[section], dict):
            raise ValueError('配置类型错误：' + section)
    for section in ('llm_configs', 'embedding_configs'):
        for name, profile in config[section].items():
            if not isinstance(name, str) or not name.strip() or not isinstance(profile, dict):
                raise ValueError('模型配置无效')
            for key in ('api_key', 'base_url', 'model_name', 'interface_format'):
                if key in profile and not isinstance(profile[key], str):
                    raise ValueError('模型文本字段类型错误：' + key)
            for key in ('max_tokens', 'timeout', 'retrieval_k'):
                if key in profile and (type(profile[key]) is not int or profile[key] <= 0):
                    raise ValueError('模型数值字段无效：' + key)
            if 'temperature' in profile and (type(profile['temperature']) not in (int, float) or not 0 <= profile['temperature'] <= 2):
                raise ValueError('temperature 必须在 0 到 2 之间')
    for key, value in config.get('webdav_config', {}).items():
        if not isinstance(value, str):
            raise ValueError('WebDAV 配置必须是文本')
    proxy = config.get('proxy_setting', {})
    if 'enabled' in proxy and type(proxy['enabled']) is not bool:
        raise ValueError('代理 enabled 必须是布尔值')
    for key in ('proxy_url', 'proxy_port'):
        if key in proxy and not isinstance(proxy[key], str):
            raise ValueError('代理地址与端口必须是文本')
    for path in _walk(config):
        if not isinstance(_get(config, path), str):
            raise ValueError('密钥必须是文本')
    for key, value in config['choose_configs'].items():
        if not isinstance(value, str) or (value and value not in config['llm_configs']):
            raise ValueError('任务模型引用不存在：' + key)
    for key, value in config['other_params'].items():
        if isinstance(value, (dict, list)) or value is None:
            raise ValueError('小说参数类型错误：' + key)
    return config

def read_raw(path):
    path = Path(path)
    raw = path.read_bytes() if path.exists() else None
    if raw is None:
        return get_default_config(), None
    try:
        data = json.loads(raw.decode('utf-8-sig'))
        validate(data)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ValueError('配置文件无效，未覆盖原文件') from exc
    return data, raw

def load(path):
    data, raw = read_raw(path)
    safe = copy.deepcopy(data)
    paths = []
    for p in _walk(safe):
        if _get(safe, p):
            paths.append(p)
        _set(safe, p, '')
    return safe, paths, digest(raw)

def update(path, config, revision, clear_secret_paths=(), copy_from=None):
    old, raw = read_raw(path)
    if revision != digest(raw):
        raise ConfigConflict('配置已更新，请重新加载')
    config = copy.deepcopy(config)
    validate(config)
    if not isinstance(clear_secret_paths, (list, tuple)):
        raise ValueError('clear_secret_paths 必须是数组')
    clear = {tuple(p) for p in clear_secret_paths if isinstance(p, (list, tuple)) and all(isinstance(k, str) for k in p)}
    paths = set(_walk(config))
    if not clear.issubset(paths):
        raise ValueError('清除密钥路径无效')
    # copy_from is [{from:[...secret path], to:[...secret path]}], for renamed profiles.
    if copy_from is not None:
        if not isinstance(copy_from, list):
            raise ValueError('copy_from 必须是数组')
        old_paths = set(_walk(old))
        for item in copy_from:
            if not isinstance(item, dict):
                raise ValueError('copy_from 无效')
            source, target = tuple(item.get('from', [])), tuple(item.get('to', []))
            if source not in old_paths or target not in paths:
                raise ValueError('copy_from 密钥路径无效')
            if not _get(config, target) and target not in clear:
                _set(config, target, _get(old, source))
    for section in ('llm_configs', 'embedding_configs'):
        old_profiles = old.get(section, {})
        for name, profile in config.get(section, {}).items():
            if name in old_profiles or not profile.get('id'):
                continue
            matches = [value for value in old_profiles.values() if value.get('id') == profile['id']]
            if len(matches) == 1 and not profile.get('api_key') and (section, name, 'api_key') not in clear:
                profile['api_key'] = matches[0].get('api_key', '')
    for p in paths:
        if p in clear:
            _set(config, p, '')
        elif not _get(config, p) and _get(old, p):
            _set(config, p, _get(old, p))
    atomic_write(path, (json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode(), raw)
    return load(path)
