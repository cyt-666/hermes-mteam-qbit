"""Restricted M-Team/qBittorrent adapter. No network requests on import."""
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath


class PluginError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PluginError('Redirect refused; verify configured service hostname.')


class HTTP:
    def __init__(self):
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, method, url, headers=None, data=None, limit=4 * 1024 * 1024):
        req = urllib.request.Request(url, headers=headers or {}, data=data, method=method)
        try:
            with self.opener.open(req, timeout=25) as response:
                result = response.read(limit + 1)
                if len(result) > limit:
                    raise PluginError('Response exceeds size limit.')
                return result
        except urllib.error.HTTPError as e:
            raise PluginError('Service HTTP error %s (URL/body omitted).' % e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise PluginError('Service connection failed or timed out; credentials/URL omitted.') from None


def base_url(value, https=False):
    p = urllib.parse.urlsplit(value)
    if p.scheme not in (('https',) if https else ('http', 'https')) or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise PluginError('Invalid service base URL.')
    return value.rstrip('/')


def endpoint(base, path):
    if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or any(x in path for x in ('?', '#', '..', '\\')):
        raise PluginError('Invalid API endpoint path.')
    return base + path


def resource_id(value):
    value = str(value)
    if not re.fullmatch(r'[0-9]{1,20}', value):
        raise PluginError('Resource ID must be numeric.')
    return value


def secret(name):
    try:
        from agent.secret_scope import get_secret
    except ImportError:
        value = os.environ.get(name, '')  # standalone offline tests / legacy CLI
    else:
        value = get_secret(name)  # preserve fail-closed multi-profile semantics
    if not value:
        raise PluginError('Required credential environment variable is unset.')
    return value


def decode_json(raw):
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise PluginError('Service returned invalid JSON.') from None


def torrent_hash(data):
    """Hash the exact bencoded info slice. Reject malformed, trailing and v2-only."""
    def parse(i, depth=0):
        if depth > 60 or i >= len(data):
            raise PluginError('Invalid torrent encoding.')
        c = data[i:i+1]
        if c == b'i':
            end = data.index(b'e', i)
            return int(data[i+1:end]), end+1
        if c in (b'd', b'l'):
            obj = {} if c == b'd' else []
            i += 1
            while data[i:i+1] != b'e':
                item, i = parse(i, depth+1)
                if c == b'd':
                    if not isinstance(item, bytes) or item in obj:
                        raise PluginError('Invalid torrent dictionary.')
                    val, i = parse(i, depth+1)
                    obj[item] = val
                else:
                    obj.append(item)
            return obj, i+1
        colon = data.index(b':', i)
        length = int(data[i:colon])
        end = colon + 1 + length
        if length < 0 or end > len(data):
            raise PluginError('Invalid torrent string.')
        return data[colon+1:end], end
    try:
        if not data.startswith(b'd'):
            raise PluginError('Response is not a torrent.')
        i, found, keys = 1, None, set()
        while data[i:i+1] != b'e':
            key, i = parse(i)
            if not isinstance(key, bytes) or key in keys:
                raise PluginError('Invalid torrent keys.')
            keys.add(key)
            start = i
            obj, i = parse(i)
            if key == b'info':
                if not isinstance(obj, dict) or b'pieces' not in obj:
                    raise PluginError('Only v1/hybrid torrents are supported in this release.')
                found = hashlib.sha1(data[start:i]).hexdigest()
        if i + 1 != len(data) or not found:
            raise PluginError('Invalid torrent envelope.')
        return found
    except (ValueError, IndexError, TypeError):
        raise PluginError('Invalid torrent encoding.') from None


class MTeam:
    def __init__(self, cfg, http):
        self.cfg, self.http = cfg, http
        self.base = base_url(cfg['base_url'], https=True)

    def api(self, path, body, form=False):
        headers = {'x-api-key': secret(self.cfg.get('api_key_env', 'MTEAM_API_KEY')),
                   'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json'}
        encoded = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        result = decode_json(self.http.request('POST', endpoint(self.base, path), headers, encoded))
        if not isinstance(result, dict) or str(result.get('code')) not in [str(c) for c in self.cfg.get('success_codes', [0])]:
            raise PluginError('M-Team rejected request or response schema changed; body omitted.')
        return result.get('data')

    @staticmethod
    def public(item):
        status = item.get('status') or {}
        # Only allow explicit fields; never leak tracker URLs/passkeys from raw responses.
        return {'id': str(item.get('id', '')), 'title': str(item.get('name', ''))[:500],
                'subtitle': str(item.get('smallDescr', ''))[:1000], 'size_bytes': item.get('size'),
                'seeders': status.get('seeders'), 'leechers': status.get('leechers'),
                'discount': status.get('discount'), 'created_at': item.get('createdDate')}

    def search(self, query, page=1, limit=20):
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise PluginError('Search query must contain 1–200 characters.')
        if not isinstance(page, int) or not isinstance(limit, int) or not 1 <= page <= 1000 or not 1 <= limit <= 50:
            raise PluginError('Invalid pagination.')
        data = self.api(self.cfg.get('search_path', '/api/torrent/search'),
                        {'mode': self.cfg.get('search_mode', 'normal'), 'keyword': query, 'pageNumber': page, 'pageSize': limit})
        if not isinstance(data, dict) or not isinstance(data.get('data'), list):
            raise PluginError('Unexpected M-Team search schema.')
        return {'items': [self.public(x) for x in data['data']], 'page': page, 'total': data.get('total')}

    def details(self, rid):
        body = dict(self.cfg.get('detail_extra', {}))
        body['id'] = resource_id(rid)
        data = self.api(self.cfg.get('detail_path', '/api/torrent/detail'), body, self.cfg.get('detail_encoding', 'form') == 'form')
        if not isinstance(data, dict):
            raise PluginError('Unexpected M-Team details schema.')
        return self.public(data)

    def torrent(self, rid):
        url = self.api(self.cfg.get('download_path', '/api/torrent/genDlToken'), {'id': resource_id(rid)}, True)
        if not isinstance(url, str):
            raise PluginError('Unexpected M-Team download schema.')
        p = urllib.parse.urlsplit(url)
        hosts = self.cfg.get('download_hosts', [])
        if p.scheme != 'https' or p.hostname not in hosts or p.username or p.password or p.fragment or p.port not in (None, 443):
            raise PluginError('Torrent URL host is not allowlisted. Verify hostname in config, never paste secret URL into chat.')
        # No API credential forwarded to download host; signed token stays inside process.
        return self.http.request('GET', url, limit=8 * 1024 * 1024)


class QBit:
    def __init__(self, cfg, http):
        self.cfg, self.http = cfg, http
        self.base = base_url(cfg['base_url'])

    def call(self, path, data=None, content_type='application/x-www-form-urlencoded'):
        return self.http.request('GET' if data is None else 'POST', self.base + '/api/v2/' + path,
                                 {'Referer': self.base + '/', 'Content-Type': content_type}, data)

    def login(self):
        data = urllib.parse.urlencode({'username': secret(self.cfg.get('username_env', 'QBIT_USERNAME')),
                                      'password': secret(self.cfg.get('password_env', 'QBIT_PASSWORD'))}).encode()
        if self.call('auth/login', data).strip() != b'Ok.':
            raise PluginError('qBittorrent login failed.')

    def info(self, digest):
        if not re.fullmatch('[a-f0-9]{40}', digest):
            raise PluginError('Invalid torrent hash.')
        result = decode_json(self.call('torrents/info?hashes=' + digest))
        if not isinstance(result, list):
            raise PluginError('Unexpected qBittorrent info schema.')
        return [{k: item.get(k) for k in ('hash', 'name', 'state', 'progress', 'dlspeed', 'eta', 'save_path')} for item in result]

    def add(self, torrent, path, category, paused):
        version = self.call('app/version').decode().strip().lstrip('v')
        if not re.match(r'^[45]\.', version):
            raise PluginError('Supported qBittorrent versions: 4.x / 5.x.')
        fields = {'savepath': path, 'category': category, 'autoTMM': 'false',
                  ('stopped' if version.startswith('5.') else 'paused'): str(paused).lower()}
        boundary = 'Hermes' + secrets.token_hex(20)
        chunks = []
        for key, value in fields.items():
            chunks.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, key, value)).encode())
        chunks += [('--%s\r\nContent-Disposition: form-data; name="torrents"; filename="resource.torrent"\r\nContent-Type: application/x-bittorrent\r\n\r\n' % boundary).encode(), torrent, ('\r\n--%s--\r\n' % boundary).encode()]
        if self.call('torrents/add', b''.join(chunks), 'multipart/form-data; boundary=' + boundary).strip() != b'Ok.':
            raise PluginError('qBittorrent did not acknowledge submission; check status before retrying.')


class App:
    def __init__(self, cfg, mt=None, qb=None):
        self.cfg = cfg
        self.destinations = cfg.get('destinations', {})
        if not self.destinations:
            raise PluginError('Configure at least one destination alias.')
        for alias, path in self.destinations.items():
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', alias) or not isinstance(path, str) or not path.startswith('/') or '..' in PurePosixPath(path).parts or any(c in path for c in '\r\n\0'):
                raise PluginError('Destination must be a named absolute POSIX path without traversal.')
        self.mt = mt or MTeam(cfg['mteam'], HTTP())
        self.qb = qb or QBit(cfg['qbittorrent'], HTTP())
        state = Path(cfg.get('state_path', '~/.hermes/mteam-qbit/state.sqlite3')).expanduser()
        state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(state, timeout=10)
        os.chmod(state, 0o600)
        self.db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, rid TEXT, title TEXT, destination TEXT, path TEXT, created REAL, state TEXT, hash TEXT, paused INTEGER, category TEXT)')
        self.db.commit()

    def prepare(self, rid, destination):
        if destination not in self.destinations:
            raise PluginError('Unknown destination alias. Use pt_destinations.')
        details = self.mt.details(resource_id(rid))
        request_id = secrets.token_hex(16)
        paused = bool(self.cfg.get('start_paused', False))
        category = self.cfg.get('category', 'hermes')
        if not isinstance(category, str) or any(c in category for c in '\r\n\0'):
            raise PluginError('Invalid category.')
        self.db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (request_id, str(rid), details['title'], destination, self.destinations[destination], time.time(), 'prepared', None, paused, category))
        self.db.commit()
        return {'request_id': request_id, 'resource': details, 'save_path': self.destinations[destination],
                'start_paused': paused, 'category': category, 'state': 'awaiting_operator_approval',
                'next_step': 'Show this exact resource and destination to the user. Submit only after their explicit selection/confirmation; never infer consent from search.'}

    def row(self, request_id):
        if not re.fullmatch('[a-f0-9]{32}', request_id):
            raise PluginError('Invalid request ID.')
        self.db.row_factory = sqlite3.Row
        row = self.db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
        if row is None:
            raise PluginError('Unknown request ID.')
        return row

    def approve(self, request_id):
        row = self.row(request_id)
        if row['state'] != 'prepared' or time.time() - row['created'] > self.cfg.get('approval_ttl_seconds', 900):
            raise PluginError('Request not approvable or expired; prepare a new one.')
        self.db.execute("UPDATE requests SET state='approved' WHERE id=? AND state='prepared'", (request_id,))
        self.db.commit()

    def submit(self, request_id, user_confirmed=False):
        row = self.row(request_id)
        if row['state'] == 'prepared':
            if user_confirmed is not True:
                raise PluginError('Explicit user confirmation of this resource and destination is required.')
            self.approve(request_id)
            row = self.row(request_id)
        self.qb.login()
        if row['hash']:
            existing = self.qb.info(row['hash'])
            if existing:
                return {'state': 'present', 'tasks': existing, 'note': 'Existing task preserved; its save path was not changed.'}
        if row['state'] in ('submitting', 'submitted', 'unknown'):
            return {'state': 'unknown', 'hash': row['hash'], 'note': 'Not visible yet. Will not resubmit automatically; inspect qBittorrent.'}
        if row['state'] != 'approved':
            raise PluginError('User confirmation is required; prepare alone does not authorize submission.')
        if time.time() - row['created'] > self.cfg.get('approval_ttl_seconds', 900):
            raise PluginError('Approval expired; prepare again.')
        if self.destinations.get(row['destination']) != row['path']:
            raise PluginError('Destination changed after approval; prepare again.')
        torrent = self.mt.torrent(row['rid'])
        digest = torrent_hash(torrent)
        # Serialize approval consumption before network side effect. Parallel calls cannot double-submit.
        count = self.db.execute("UPDATE requests SET state='submitting',hash=? WHERE id=? AND state='approved'", (digest, request_id)).rowcount
        self.db.commit()
        if not count:
            return {'state': 'busy', 'note': 'Another call already consumed approval.'}
        existing = self.qb.info(digest)
        if existing:
            return {'state': 'present', 'tasks': existing, 'note': 'Existing task preserved; its save path was not changed.'}
        try:
            self.qb.add(torrent, row['path'], row['category'], bool(row['paused']))
        except Exception:
            self.db.execute("UPDATE requests SET state='unknown' WHERE id=?", (request_id,))
            self.db.commit()
            raise PluginError('Submission outcome uncertain; check status. No automatic resend.') from None
        self.db.execute("UPDATE requests SET state='submitted' WHERE id=?", (request_id,))
        self.db.commit()
        tasks = self.qb.info(digest)
        return {'state': 'present' if tasks else 'submitted_not_yet_visible', 'hash': digest, 'tasks': tasks}

    def status(self, request_id):
        row = self.row(request_id)
        if not row['hash']:
            return {'state': row['state'], 'tasks': []}
        self.qb.login()
        return {'state': row['state'], 'hash': row['hash'], 'tasks': self.qb.info(row['hash'])}


def load_app():
    try:
        from .settings import config_path
        path = config_path()
    except ImportError:
        path = Path(os.environ.get('HERMES_PT_CONFIG', '~/.hermes/mteam-qbit/config.json')).expanduser()
    try:
        cfg = json.loads(path.read_text())
    except (OSError, ValueError):
        raise PluginError('请先在 Hermes 网页的 M-Team 下载页面保存配置。') from None
    return App(cfg)
