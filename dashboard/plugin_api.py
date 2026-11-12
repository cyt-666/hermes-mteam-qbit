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
PACKAGE = '_mteam_qbit_dashboard_' + __import__('hashlib').sha256(str(ROOT).encode()).hexdigest()[:12]
if PACKAGE not in sys.modules:
    spec = importlib.util.spec_from_file_location(PACKAGE, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
settings = __import__(PACKAGE + '.settings', fromlist=['settings'])
core = __import__(PACKAGE + '.core', fromlist=['core'])
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
                            'compatibility': 'Hermes v0.23.1 Dashboard SDK 1.1.0; live deployment not certified'})


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
        mt = core.MTeam(cfg['mteam'], core.HTTP())
        mt.search('hermes-connection-test', limit=1)
        return {'ok': True, 'message': '馒头认证与搜索接口响应正常。未获取种子或提交任务。'}
    raise core.PluginError('未知测试目标。')


@router.post('/test/{target}')
def test_connection(target: str):
    return guarded(probe, target)
