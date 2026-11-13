"""Profile-scoped configuration used by the Dashboard and the Agent.

Secrets use Hermes's credential lifecycle; only non-secrets go to our JSON.
"""
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from .core import PluginError, base_url, endpoint, save_path, configured_default, SEARCH_MODES

SECRET_FIELDS = {'mteam_api_key': 'MTEAM_API_KEY', 'qbit_username': 'QBIT_USERNAME', 'qbit_password': 'QBIT_PASSWORD'}
DEFAULTS = {
    'mteam_url': 'https://api.m-team.cc', 'mteam_user_agent': 'MTeamQBit/1.0', 'qbit_url': 'http://qbittorrent:8080',
    'download_hosts': ['api.m-team.cc'], 'default_save_path': '/downloads',
    'category': 'hermes', 'start_paused': False, 'approval_ttl_seconds': 900,
    'search_path': '/api/torrent/search', 'detail_path': '/api/torrent/detail',
    'download_path': '/api/torrent/genDlToken', 'detail_encoding': 'form',
    'detail_origin': '', 'search_mode': 'normal'
}


def config_path():
    # Follow the active Hermes profile, not process-global HOME/HERMES_HOME.
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home()) / 'mteam-qbit' / 'config.json'


def validate(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise PluginError('Unknown or invalid settings fields.')
    v = {**DEFAULTS, **values}
    v['mteam_url'] = base_url(v['mteam_url'], https=True)
    v['qbit_url'] = base_url(v['qbit_url'])
    if not isinstance(v['mteam_user_agent'], str) or not 1 <= len(v['mteam_user_agent']) <= 256 or any(ord(c) < 32 or ord(c) > 126 for c in v['mteam_user_agent']):
        raise PluginError('User-Agent 需为 1–256 位可打印 ASCII 字符。')
    for k in ('search_path', 'detail_path', 'download_path'):
        endpoint(v['mteam_url'], v[k])
    if v['detail_encoding'] not in ('form', 'json'):
        raise PluginError('详情请求格式必须是 form 或 json。')
    for k in ('category', 'detail_origin', 'search_mode'):
        if not isinstance(v[k], str) or len(v[k]) > 100 or any(c in v[k] for c in '\r\n\0'):
            raise PluginError('类别/模式/来源字段无效。')
    if v['search_mode'] not in SEARCH_MODES:
        raise PluginError('搜索模式不在支持的接口枚举中。')
    if type(v['start_paused']) is not bool or type(v['approval_ttl_seconds']) is not int or not 60 <= v['approval_ttl_seconds'] <= 86400:
        raise PluginError('暂停开关或确认有效期无效（60–86400 秒）。')
    if not isinstance(v['download_hosts'], list) or not 1 <= len(v['download_hosts']) <= 20 or not all(isinstance(h, str) and re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', h) for h in v['download_hosts']):
        raise PluginError('下载域名需要精确域名列表，不带协议/路径/通配符。')
    v['download_hosts'] = [h.lower() for h in v['download_hosts']]
    v['default_save_path'] = save_path(v['default_save_path'])
    return v


def to_config(values, path):
    v = validate(values)
    return {
        'mteam': {'base_url': v['mteam_url'], 'user_agent': v['mteam_user_agent'], 'api_key_env': 'MTEAM_API_KEY', 'download_hosts': v['download_hosts'],
                  'search_path': v['search_path'], 'detail_path': v['detail_path'], 'download_path': v['download_path'],
                  'detail_encoding': v['detail_encoding'], 'detail_extra': {'origin': v['detail_origin']} if v['detail_origin'] else {},
                  'search_mode': v['search_mode']},
        'qbittorrent': {'base_url': v['qbit_url'], 'username_env': 'QBIT_USERNAME', 'password_env': 'QBIT_PASSWORD'},
        'default_save_path': v['default_save_path'], 'category': v['category'], 'start_paused': v['start_paused'],
        'approval_ttl_seconds': v['approval_ttl_seconds'], 'state_path': str(path.parent / 'state.sqlite3')
    }


def public_values(cfg):
    m, q = cfg.get('mteam', {}), cfg.get('qbittorrent', {})
    result = dict(DEFAULTS)
    result['mteam_user_agent'] = m.get('user_agent', DEFAULTS['mteam_user_agent'])
    result.update(mteam_url=m.get('base_url', DEFAULTS['mteam_url']), qbit_url=q.get('base_url', DEFAULTS['qbit_url']),
                  download_hosts=m.get('download_hosts', DEFAULTS['download_hosts']),
                  detail_encoding=m.get('detail_encoding', 'form'), detail_origin=m.get('detail_extra', {}).get('origin', ''),
                  search_mode=m.get('search_mode', 'normal'))
    for k in ('search_path', 'detail_path', 'download_path'):
        result[k] = m.get(k, DEFAULTS[k])
    for k in ('category', 'start_paused', 'approval_ttl_seconds'):
        result[k] = cfg.get(k, DEFAULTS[k])
    result['default_save_path'] = configured_default(cfg)
    # Do not return entire config: legacy manually added unknown secrets stay private.
    # Keep retired defaults visible/editable without silently changing the saved search scope.
    retired = result['search_mode'] if result['search_mode'] in ('waterfall', 'rss') else None
    if retired:
        result['search_mode'] = 'normal'
    validated = validate(result)
    if retired:
        validated['search_mode'] = retired
    return validated


def read_settings():
    path = config_path()
    if not path.exists():
        return dict(DEFAULTS)
    try:
        return public_values(json.loads(path.read_text()))
    except (OSError, ValueError, TypeError, AttributeError):
        raise PluginError('无法读取现有配置；请检查日志/备份，未覆盖原文件。') from None


def secret_presence():
    from agent.secret_scope import get_secret
    return {field: bool(get_secret(env)) for field, env in SECRET_FIELDS.items()}


def save_settings(values, secrets_update=None):
    path = config_path()
    cfg = to_config(values, path)
    updates = secrets_update or {}
    if not isinstance(updates, dict) or set(updates) - set(SECRET_FIELDS):
        raise PluginError('未知凭据字段。')
    for value in updates.values():
        if not isinstance(value, str) or len(value) > 8192 or any(c in value for c in '\r\n\0'):
            raise PluginError('凭据格式无效。')
    # Empty fields preserve existing secrets. No secret reads/reveal endpoint.
    from hermes_cli.credential_lifecycle import save_provider_env_credential
    try:
        for field, value in updates.items():
            if value:
                save_provider_env_credential(SECRET_FIELDS[field], value)
    except Exception:
        raise PluginError('凭据保存失败；部分字段可能已保存，请刷新后检查。普通配置未保存。') from None
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.config-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(cfg, out, ensure_ascii=False, indent=2)
            out.write('\n')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return {'ok': True, 'message': '已保存；新调用读取新配置。凭据由 Hermes 管理，不会返回浏览器。'}
