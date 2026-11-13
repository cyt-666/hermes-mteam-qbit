"""Hermes v0.23.1 authenticated/profile-scoped Dashboard plugin routes.

Only load through Hermes's plugin mount. There is no separate public server.
"""
import asyncio
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request

# Hermes imports plugin_api.py in an isolated module namespace, not as our package.
# Give relative imports a unique package without altering sys.path.
ROOT = Path(__file__).resolve().parents[1]
# Namespace by source snapshot, not just installation path. Compile the snapshot
# directly: timestamp-based .pyc may otherwise reuse same-size rapid edits.
import hashlib
import types
_BOOTSTRAP = sys.modules.setdefault('_mteam_qbit_bootstrap_lock', types.ModuleType('_mteam_qbit_bootstrap_lock'))
_BOOTSTRAP.__dict__.setdefault('lock', threading.RLock())
_SOURCE_NAMES = ('__init__.py', 'core.py', 'settings.py')
_sources = {name: (ROOT / name).read_bytes() for name in _SOURCE_NAMES}
_digest = hashlib.sha256()
for _name, _source in _sources.items():
    _digest.update(_name.encode() + b'\0' + _source + b'\0')
BUSINESS_BUILD = _digest.hexdigest()
PACKAGE = '_mteam_qbit_dashboard_' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:12] + '_' + BUSINESS_BUILD
with _BOOTSTRAP.lock:
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]
        package.__package__ = PACKAGE
        package.__file__ = str(ROOT / '__init__.py')
        sys.modules[PACKAGE] = package
        try:
            for name in ('core', 'settings'):
                child = types.ModuleType(PACKAGE + '.' + name)
                child.__package__ = PACKAGE
                child.__file__ = str(ROOT / (name + '.py'))
                sys.modules[child.__name__] = child
                exec(compile(_sources[name + '.py'], child.__file__, 'exec'), child.__dict__)
                setattr(package, name, child)
            exec(compile(_sources['__init__.py'], package.__file__, 'exec'), package.__dict__)
            package.__business_build__ = BUSINESS_BUILD
        except BaseException:
            # Only remove this incomplete generation. Existing handlers retain
            # their old generation; no reload/pop of shared live modules.
            for key in (PACKAGE + '.settings', PACKAGE + '.core', PACKAGE):
                sys.modules.pop(key, None)
            raise
    package = sys.modules[PACKAGE]
    core, settings = package.core, package.settings
    BUSINESS_BUILD = package.__business_build__
router = APIRouter()
_probe_lock = threading.Lock()
_probe_time = {}


def guarded(fn, *args):
    try:
        return fn(*args)
    except core.PluginError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception:
        # Never include exception values: they may carry credentials/URLs.
        raise HTTPException(status_code=500, detail='操作失败；敏感错误详情未返回。请检查配置及 Hermes 兼容版本。') from None


@router.get('/settings')
def get_settings():
    return guarded(lambda: {'settings': settings.read_settings(), 'secrets_set': settings.secret_presence(),
                            'backend_version': '0.6.0', 'business_build': BUSINESS_BUILD, 'compatibility': 'Hermes v0.23.1 Dashboard SDK 1.1.0; live deployment not certified'})


@router.put('/settings')
async def put_settings(request: Request):
    body = await request.body()
    if len(body) > 65536:
        raise HTTPException(status_code=413, detail='配置请求过大。')
    try:
        data = json.loads(body)
        if not isinstance(data, dict) or set(data) - {'settings', 'secrets'}:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=400, detail='配置请求格式无效。') from None
    return await asyncio.to_thread(guarded, settings.save_settings, data.get('settings'), data.get('secrets'))


def probe(target):
    key = (str(settings.config_path()), target)
    with _probe_lock:
        if time.monotonic() - _probe_time.get(key, -100) < 15:
            raise core.PluginError('请等待 15 秒再测试同一服务。')
        _probe_time[key] = time.monotonic()
    # Only saved settings are used; no arbitrary request-supplied URL.
    cfg = settings.to_config(settings.read_settings(), settings.config_path())
    if target == 'qbit':
        qb = core.QBit(cfg['qbittorrent'], core.HTTP())
        qb.login()
        version = qb.call('app/version').decode().strip()
        if not __import__('re').fullmatch(r'v?[45]\.\d+(?:\.\d+)?(?:[A-Za-z0-9.-]*)', version):
            raise core.PluginError('qBittorrent 版本响应不在支持范围。')
        return {'ok': True, 'message': 'qBittorrent 登录成功，版本 ' + version + '。未添加任务；保存目录写权限尚未验证。'}
    if target == 'mteam':
        mt = core.MTeam(cfg['mteam'], core.HTTP(cookies=False))
        mt.test_connection()
        return {'ok': True, 'message': '馒头账户连接接口响应正常，未返回账户资料。此测试不验证搜索接口，未获取种子或提交任务。'}
    raise core.PluginError('未知测试目标。')


def probe_error(exc):
    # Only locally defined messages/codes reach the page. Never echo upstream bodies,
    # exception strings, URLs, or arbitrary PluginError text.
    allowed_reasons = {'redirect_refused','dns_error','tls_certificate','tls_error','timeout',
                       'connection_refused','connection_reset','network_error','response_too_large','http_error','unexpected_error'}
    if isinstance(exc, (core.TransportError, core.StageError)) and exc.reason in allowed_reasons:
        return {'ok':False, 'code':exc.reason,
                'message':'连接失败：' + exc.reason + '。请核对网络、服务地址与证书；未返回敏感原文。'}
    text = str(exc)
    messages = {
        'response_shape': ('response_shape', '馒头返回 JSON，但顶层不是预期对象。未返回原始内容。'),
        'response_code_missing': ('response_code_missing', '馒头返回对象缺少业务 code 字段，不能判断认证成功。'),
        'business_rejected': ('business_rejected', '馒头返回非成功业务码；请结合受限数字码核对站点，不返回原始说明或账户资料。'),
        'Unexpected M-Team profile schema.': ('profile_schema', '业务码成功，但账户数据为空或结构不兼容，不能确认连接测试成功。'),
        'Required credential environment variable is unset.': ('credential_missing', '当前 Profile 未读取到必需凭据。请先保存，再测试；核对 API Key 或 qBit 用户名/密码显示已设置。'),
        'Service connection failed or timed out; credentials/URL omitted.': ('network', '连接失败或超时。检查 Hermes 容器的 DNS、网络、代理与服务地址；不是已确认 API Key 错误。'),
        'Service returned invalid JSON.': ('invalid_json', '服务未返回 JSON，可能填了网页地址、代理/防护页或网关错误；请核对 API 地址。'),
        'Redirect refused; verify configured service hostname.': ('redirect', '服务要求跳转，插件未转发凭据。请使用站点公布的当前 API 地址。'),
        'M-Team rejected request or response schema changed; body omitted.': ('mteam_rejected', '馒头返回业务拒绝或未知结构。可能是 Key、账户权限、限流或接口变化；需要核对站点 API 设置。'),
        'Unexpected M-Team search schema.': ('search_schema', '认证请求返回成功，但搜索结果结构不兼容。请报告此错误码。'),
        '请等待 15 秒再测试同一服务。': ('rate_limit', '请等待 15 秒再测试同一服务。'),
    }
    if text in messages:
        code, message = messages[text]
    elif __import__('re').fullmatch(r'Service HTTP error [1-5][0-9]{2} \(URL/body omitted\).', text):
        status = text.split()[3]
        code, message = 'http_' + status, '服务返回 HTTP ' + status + '。401/403 常涉及认证或访问防护；404 检查 API 地址与路径；429 请稍后再试。未返回响应正文。'
    else:
        code, message = 'probe_failed', '连接测试失败，敏感详情已隐藏。请核对已保存配置、当前 Profile 与 Hermes 版本。'
    return {'ok': False, 'code': code, 'message': message}


def credential_diagnostics(target):
    # Compare in this request's current profile secret scope. Never export even
    # a fingerprint, prefix, length, source name, or exception text.
    if target != 'mteam':
        return None
    try:
        from agent.secret_scope import get_secret, load_env_file
        effective = get_secret('MTEAM_API_KEY')
        saved = load_env_file(settings.config_path().parent.parent / '.env').get('MTEAM_API_KEY')
        return {'effective_present': bool(effective), 'profile_saved_present': bool(saved),
                'effective_matches_saved': effective == saved if effective and saved else None}
    except Exception:
        return {'effective_present': None, 'profile_saved_present': None, 'effective_matches_saved': None}


@router.post('/test/{target}')
def test_connection(target: str):
    diagnostics = credential_diagnostics(target)
    try:
        result = probe(target)
    except Exception as exc:
        result = probe_error(exc)
        if isinstance(exc, core.MTeamResponseError):
            result['response_diagnostics'] = exc.response_diagnostics
        if isinstance(exc, core.ServiceHTTPError):
            result['http_diagnostics'] = exc.http_diagnostics
    if diagnostics is not None:
        result['credential_diagnostics'] = diagnostics
    return result
