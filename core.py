"""Restricted M-Team/qBittorrent adapter. No network requests on import."""
import hashlib
import http.cookiejar
import json
import math
import os
import re
import secrets
import socket
import ssl
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath


class PluginError(Exception):
    pass


SEARCH_MODES = ('normal', 'adult', 'movie', 'music', 'tvshow', 'anime', 'rankings', 'all')

class StageError(PluginError):
    def __init__(self, stage, status=None, uncertain=False, reason=None):
        self.stage = stage
        self.http_status = status
        self.uncertain = uncertain
        self.reason = reason
        message = 'Failed at ' + stage + (': HTTP ' + str(status) if status else '.')
        if reason: message += ' Reason: ' + reason + '.'
        if uncertain: message += ' Submission outcome uncertain; check status. No automatic resend.'
        super().__init__(message)

    def diagnostics(self):
        return {'stage':self.stage, 'http_status':self.http_status,
                'submission_outcome_uncertain':self.uncertain, 'reason':self.reason}


def stage_call(stage, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except StageError:
        raise
    except Exception as error:
        status = error.http_diagnostics['status'] if isinstance(error, ServiceHTTPError) else error.status if isinstance(error, TransportError) else None
        reason = error.reason if isinstance(error, TransportError) else 'http_error' if isinstance(error, ServiceHTTPError) else 'unexpected_error'
        raise StageError(stage, status, reason=reason) from None


class ServiceHTTPError(PluginError):
    def __init__(self, status, headers):
        super().__init__('Service HTTP error %s (URL/body omitted).' % status)
        # Only categorical/boolean diagnostics. Never retain body, URL or headers.
        content_type = (headers.get('Content-Type', '') if headers else '').split(';', 1)[0].strip().lower()
        self.http_diagnostics = {
            'status': int(status),
            'response_type': 'json' if content_type == 'application/json' or content_type.endswith('+json') else 'html' if content_type in ('text/html', 'application/xhtml+xml') else 'other',
            'cloudflare_header_present': bool(headers and headers.get('cf-ray')),
            'cloudflare_server': bool(headers and headers.get('Server', '').lower().strip() == 'cloudflare'),
            'challenge_header': bool(headers and headers.get('cf-mitigated', '').lower().strip() == 'challenge'),
        }



def sanitized_upstream_message(result, known_secrets=()):
    """Bounded textual diagnostic, with fail-closed credential assignments."""
    import html
    value = result.get('message') if isinstance(result.get('message'), str) else result.get('msg')
    if not isinstance(value, str) or not value:
        return None
    if len(value) > 8192 or re.search(r'<(?:!doctype|html|script)\b', value, re.I):
        return '[message omitted: oversized or HTML response]'
    # Normalize before redacting so tag/control removal cannot reassemble secrets.
    for _ in range(2):
        value = html.unescape(value)
        value = re.sub(r'<[^>]*>', '', value)
        value = ''.join(c for c in value if ord(c) >= 32 and ord(c) != 127)
    # Credential assignments can contain quoted whitespace/arbitrary syntax.
    # Do not attempt partial extraction: suppress the entire message.
    if re.search(r"(?i)(?:api[-_ ]?key|passkey|password|passwd|token|authorization|cookie|secret|sid|密钥|密码)[\"']?\s*[=:：]", value):
        return '[message omitted: credential-bearing diagnostic]'
    variants = []
    for secret_value in known_secrets:
        if isinstance(secret_value, str) and secret_value:
            variants.extend((secret_value, urllib.parse.quote(secret_value, safe=''), urllib.parse.quote_plus(secret_value)))
    for variant in variants:
        value = value.replace(variant, '[REDACTED]')
    value = re.sub(r'https?://[^\s<>"\']+', '[URL REDACTED]', value, flags=re.I)
    value = re.sub(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}', '[EMAIL REDACTED]', value, flags=re.I)
    if re.search(r'(?i)bearer\s+', value):
        return '[message omitted: credential-bearing diagnostic]'
    value = re.sub(r'[A-Za-z0-9_+/=-]{20,}', '[OPAQUE VALUE REDACTED]', value)
    # Final guard after all transformations, before truncating.
    for variant in variants:
        value = value.replace(variant, '[REDACTED]')
    return value[:300] + ('…' if len(value) > 300 else '')


class MTeamResponseError(PluginError):
    def __init__(self, category, status, code=None, upstream_message=None):
        super().__init__(category)
        typ = 'unavailable' if category == 'response_shape' else 'missing' if category == 'response_code_missing' else 'boolean' if isinstance(code, bool) else 'number' if isinstance(code, (int, float)) else 'string' if isinstance(code, str) else 'null' if code is None else 'other'
        numeric = str(code) if type(code) is int or isinstance(code, str) else ''
        self.response_diagnostics = {'http_status': status if type(status) is int and 100 <= status <= 599 else None,
                                     'upstream_message': upstream_message,
                                     'code_type': typ, 'numeric_code': numeric if re.fullmatch(r'[0-9]{1,6}', numeric) else None}


class TransportError(PluginError):
    def __init__(self, reason, status=None):
        self.reason, self.status = reason, status
        super().__init__('Service transport failure: ' + reason + '; URL/details omitted.')


def transport_reason(error):
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError): return 'tls_certificate'
    if isinstance(reason, ssl.SSLError): return 'tls_error'
    if isinstance(reason, (TimeoutError, socket.timeout)): return 'timeout'
    if isinstance(reason, socket.gaierror): return 'dns_error'
    if isinstance(reason, ConnectionRefusedError): return 'connection_refused'
    if isinstance(reason, (ConnectionResetError, BrokenPipeError)): return 'connection_reset'
    return 'network_error'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if fp is not None: fp.close()
        raise TransportError('redirect_refused', int(code))


class HTTP:
    def __init__(self, cookies=True):
        self.last_status = None
        handlers = [NoRedirect()]
        if cookies: handlers.append(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.opener = urllib.request.build_opener(*handlers)

    def request(self, method, url, headers=None, data=None, limit=4 * 1024 * 1024):
        self.last_status = None
        req = urllib.request.Request(url, headers=headers or {}, data=data, method=method)
        try:
            with self.opener.open(req, timeout=25) as response:
                self.last_status = response.status
                result = response.read(limit + 1)
                if len(result) > limit:
                    raise TransportError('response_too_large', self.last_status)
                return result
        except urllib.error.HTTPError as e:
            error = ServiceHTTPError(e.code, e.headers)
            e.close()
            raise error from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TransportError(transport_reason(error), self.last_status) from None


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
                   'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json',
                   'User-Agent': self.cfg.get('user_agent', 'MTeamQBit/1.0')}
        if body is None:
            headers.pop('Content-Type', None)
            headers['Accept'] = 'application/json, text/plain, */*'
        encoded = None if body is None else urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        result = decode_json(self.http.request('POST', endpoint(self.base, path), headers, encoded))
        status = getattr(self.http, 'last_status', None)
        if not isinstance(result, dict):
            raise MTeamResponseError('response_shape', status)
        if 'code' not in result:
            raise MTeamResponseError('response_code_missing', status)
        if str(result['code']) not in [str(c) for c in self.cfg.get('success_codes', [0])]:
            raise MTeamResponseError('business_rejected', status, result['code'], sanitized_upstream_message(result, (headers['x-api-key'],)))
        return result.get('data')

    def test_connection(self):
        data = self.api('/api/member/profile', None)
        if not isinstance(data, dict) or not data:
            raise PluginError('Unexpected M-Team profile schema.')
        # Never return profile contents to the UI/model.
        return True

    @staticmethod
    def public(item):
        status = item.get('status') or {}
        status = status if isinstance(status, dict) else {}
        category = item.get('category')
        if type(category) is int: category = str(category)
        category = category if isinstance(category, str) and re.fullmatch(r'[0-9]{1,10}', category) else None
        # Only allow explicit fields; never leak tracker URLs/passkeys from raw responses.
        return {'id': str(item.get('id', '')), 'category_id': category, 'title': str(item.get('name', ''))[:500],
                'subtitle': str(item.get('smallDescr', ''))[:1000], 'size_bytes': item.get('size'),
                'seeders': status.get('seeders'), 'leechers': status.get('leechers'),
                'discount': status.get('discount'), 'created_at': item.get('createdDate')}

    def categories(self):
        """Read server-provided metadata; no fixed category IDs or stale fallback."""
        data = self.api('/api/torrent/categoryList', None)
        if not isinstance(data, dict) or not isinstance(data.get('list'), list) or len(data['list']) > 2000:
            raise PluginError('Unexpected M-Team category schema.')
        def ident(value):
            if type(value) is int: value = str(value)
            return value if isinstance(value, str) and re.fullmatch(r'[0-9]{1,10}', value) else None
        rows, seen = [], set()
        for item in data['list']:
            if not isinstance(item, dict) or not ident(item.get('id')):
                raise PluginError('Unexpected M-Team category schema.')
            rid = ident(item['id'])
            if rid in seen: raise PluginError('Duplicate M-Team category ID.')
            seen.add(rid)
            row = {'id':rid, 'parent':ident(item.get('parent'))}
            for field in ('nameChs', 'nameCht', 'nameEng'):
                value = item.get(field)
                if isinstance(value, str) and value.strip() and len(value) <= 200:
                    row[field] = ''.join(c for c in value if ord(c) >= 32 and ord(c) != 127)
            if len(row) == 2: raise PluginError('Unexpected M-Team category name.')
            rows.append(row)
        groups = {}
        for key, values in data.items():
            if key not in ('adult', 'movie', 'music', 'tvshow', 'waterfall'): continue
            if isinstance(values, list) and values and len(values) <= 2000 and all(ident(x) for x in values):
                known = list(dict.fromkeys(ident(x) for x in values if ident(x) in seen))
                if known: groups[key] = known
        return {'source':'live /api/torrent/categoryList', 'categories':rows, 'groups':groups,
                'search_mode':self.cfg.get('search_mode','normal'),
                'scope':'Server-returned catalog for this account; visibility/search results depend on account permissions and configured mode.'}

    def resolve_category(self, category):
        if not isinstance(category, str):
            raise PluginError('Category must be a string from pt_categories.')
        if category in ('all', '全部'): return []
        if re.fullmatch(r'[0-9]{1,10}', category): return [category]
        catalog = self.categories()
        if category in catalog['groups']: return catalog['groups'][category]
        matches = [row['id'] for row in catalog['categories']
                   if any(row.get(field) == category for field in ('nameChs','nameCht','nameEng'))]
        if len(matches) != 1:
            raise PluginError('Category name unknown or ambiguous. Use pt_categories and explicit category_ids.')
        # Resolve a parent to its leaf descendants, cycle-safe, from the live tree.
        children = {}
        for row in catalog['categories']:
            children.setdefault(row['parent'], []).append(row['id'])
        pending, visited, leaves = matches[:], set(), []
        while pending:
            rid = pending.pop(0)
            if rid in visited: raise PluginError('Invalid cyclic M-Team category tree.')
            visited.add(rid)
            if children.get(rid): pending.extend(children[rid])
            else: leaves.append(rid)
        return leaves

    def search(self, query, page=1, limit=20, category='all', category_ids=None, mode=None):
        mode = self.cfg.get('search_mode', 'normal') if mode is None else mode
        if not isinstance(mode, str) or mode not in SEARCH_MODES:
            raise PluginError('Unsupported M-Team search mode.')
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise PluginError('Search query must contain 1–200 characters.')
        if type(page) is not int or type(limit) is not int or not 1 <= page <= 1000 or not 1 <= limit <= 50:
            raise PluginError('Pagination requires page 1–1000 and limit 1–50.')
        # Explicit IDs take precedence, including when replaying category='custom'
        # returned by the previous page.
        if category_ids is not None:
            if (not isinstance(category_ids, list) or not 1 <= len(category_ids) <= 2000
                    or any(not isinstance(x, str) or not re.fullmatch(r'[0-9]{1,10}', x) for x in category_ids)):
                raise PluginError('category_ids requires 1–2000 numeric string IDs.')
            categories = list(dict.fromkeys(category_ids))
        else:
            categories = self.resolve_category(category)
        data = self.api(self.cfg.get('search_path', '/api/torrent/search'),
                        {'mode': mode, 'keyword': query,
                         'categories': categories, 'pageNumber': page, 'pageSize': limit})
        if not isinstance(data, dict) or not isinstance(data.get('data'), list):
            raise PluginError('Unexpected M-Team search schema.')
        def count(value):
            if type(value) is int and value >= 0: return value
            if isinstance(value, str) and re.fullmatch(r'[0-9]{1,12}', value): return int(value)
            return None
        reported_page = count(data.get('pageNumber'))
        current = reported_page if reported_page and reported_page <= 1000 else page
        total, total_pages = count(data.get('total')), count(data.get('totalPages'))
        reported_limit = count(data.get('pageSize'))
        actual_limit = reported_limit if reported_limit and reported_limit <= 10000 else limit
        # Do not fabricate an end-of-list from a short page when server metadata is absent.
        has_more = current < total_pages if total_pages is not None else current * actual_limit < total if total is not None else None
        return {'items': [self.public(x) for x in data['data'] if isinstance(x, dict)],
                'query': query, 'mode': mode, 'category': category if category_ids is None else 'custom', 'category_ids': categories,
                'requested_page': page, 'page': current, 'requested_limit': limit, 'limit': actual_limit, 'total': total, 'total_pages': total_pages,
                'has_more': has_more, 'next_page': current + 1 if has_more is True and current < 1000 else None,
                'page_limit_reached': current >= 1000, 'previous_page': current - 1 if current > 1 else None}

    def details(self, rid):
        body = dict(self.cfg.get('detail_extra', {}))
        body['id'] = resource_id(rid)
        data = self.api(self.cfg.get('detail_path', '/api/torrent/detail'), body, self.cfg.get('detail_encoding', 'form') == 'form')
        if not isinstance(data, dict):
            raise PluginError('Unexpected M-Team details schema.')
        return self.public(data)

    def download_url(self, rid):
        url = stage_call('mteam_download_link', self.api, self.cfg.get('download_path', '/api/torrent/genDlToken'), {'id': resource_id(rid)}, True)
        if not isinstance(url, str):
            raise PluginError('Unexpected M-Team download schema.')
        p = urllib.parse.urlsplit(url)
        hosts = self.cfg.get('download_hosts', [])
        if p.scheme != 'https' or p.hostname not in hosts or p.username or p.password or p.fragment or p.port not in (None, 443):
            raise PluginError('Torrent URL host is not allowlisted. Verify hostname in config, never paste secret URL into chat.')
        if any(ord(c) < 33 or ord(c) == 127 for c in url):
            raise PluginError("Invalid signed URL.")
        return url

    def torrent(self, rid):
        # Legacy transport helper; URL submissions do not invoke this method.
        url = self.download_url(rid)
        # No API credential forwarded to download host; signed token stays inside process.
        return stage_call('mteam_torrent_fetch', self.http.request, 'GET', url, {'User-Agent': self.cfg.get('user_agent', 'MTeamQBit/1.0')}, limit=8 * 1024 * 1024)


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
        if stage_call('qbit_login', self.call, 'auth/login', data).strip() != b'Ok.':
            raise PluginError('qBittorrent login failed.')

    def info(self, digest):
        if not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', digest):
            raise PluginError('Invalid torrent hash.')
        result = decode_json(stage_call('qbit_task_lookup', self.call, 'torrents/info?hashes=' + digest))
        if not isinstance(result, list):
            raise PluginError('Unexpected qBittorrent info schema.')
        return [{k: item.get(k) for k in ('hash', 'name', 'state', 'progress', 'dlspeed', 'eta', 'save_path')} for item in result if isinstance(item, dict) and item.get('hash') == digest]

    def delete(self, digest, delete_files=False):
        if not isinstance(digest, str) or not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', digest):
            raise PluginError('One exact torrent hash is required.')
        if type(delete_files) is not bool:
            raise PluginError('delete_files must be boolean.')
        data = urllib.parse.urlencode({'hashes':digest,'deleteFiles':str(delete_files).lower()}).encode()
        stage_call('qbit_delete', self.call, 'torrents/delete', data)

    def list_tasks(self, page=1, limit=20):
        if type(page) is not int or not 1 <= page <= 1000 or type(limit) is not int or not 1 <= limit <= 100:
            raise PluginError('page must be 1–1000 and limit must be 1–100.')
        query = urllib.parse.urlencode({'sort': 'added_on', 'reverse': 'true',
                                       'offset': (page - 1) * limit, 'limit': limit + 1})
        result = decode_json(stage_call('qbit_task_lookup', self.call, 'torrents/info?' + query))
        if not isinstance(result, list):
            raise PluginError('Unexpected qBittorrent info schema.')
        def text(value):
            if not isinstance(value, str): return None
            # Task names/paths are untrusted. Do not echo embedded URLs, magnets or credential assignments.
            value = re.sub(r'(?i)(?:[a-z][a-z0-9+.-]*://|magnet:)[^\s<>"\']+', '[URL REDACTED]', value)
            value = sanitized_upstream_message({'message': value})
            return re.sub(r'(?i)(?:[a-z][a-z0-9+.-]*://|magnet:)[^\s<>"\']+', '[URL REDACTED]', value) if value else value
        def number(value):
            return value if type(value) in (int, float) and 0 <= value <= 1e18 and math.isfinite(value) else None
        items = []
        for item in result[:limit]:
            if not isinstance(item, dict): continue
            digest = item.get('hash')
            digest = digest.lower() if isinstance(digest, str) and re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', digest) else None
            projected = {'hash': digest}
            for key in ('name', 'state', 'save_path', 'category'):
                projected[key] = text(item.get(key))
            for key in ('progress', 'size', 'total_size', 'downloaded', 'uploaded', 'dlspeed', 'upspeed', 'eta', 'added_on'):
                projected[key] = number(item.get(key))
            if projected['progress'] is not None and projected['progress'] > 1:
                projected['progress'] = None
            items.append(projected)
        has_more = len(result) > limit
        return {'items': items, 'page': page, 'limit': limit, 'has_more': has_more,
                'next_page': page + 1 if has_more and page < 1000 else None,
                'previous_page': page - 1 if page > 1 else None,
                'page_limit_reached': page == 1000, 'total': None}

    def info_tag(self, tag):
        if not re.fullmatch(r'mteam-qbit-(?:r[0-9]+|[a-f0-9]{32})', tag):
            raise PluginError('Invalid task tag.')
        result = decode_json(stage_call('qbit_task_lookup', self.call, 'torrents/info?tag=' + tag))
        if not isinstance(result, list):
            raise PluginError('Unexpected qBittorrent info schema.')
        return [{k: item.get(k) for k in ('hash', 'name', 'state', 'progress', 'dlspeed', 'eta', 'save_path')}
                for item in result if isinstance(item, dict) and isinstance(item.get('tags'), str)
                and tag in [value.strip() for value in item['tags'].split(',')]]

    def add_url(self, url, path, category, paused, request_id, rid):
        version = stage_call('qbit_version', self.call, 'app/version').decode().strip().lstrip('v')
        if not re.match(r'^[45]\.', version):
            raise PluginError('Supported qBittorrent versions: 4.x / 5.x.')
        fields = {'urls': url, 'savepath': path, 'category': category, 'autoTMM': 'false',
                  'tags': 'mteam-qbit-' + request_id + ',mteam-qbit-r' + resource_id(rid),
                  ('stopped' if version.startswith('5.') else 'paused'): str(paused).lower()}
        if stage_call('qbit_add', self.call, 'torrents/add', urllib.parse.urlencode(fields).encode()).strip() != b'Ok.':
            raise PluginError('qBittorrent did not acknowledge submission; check status before retrying.')

    def add(self, torrent, path, category, paused):
        version = stage_call('qbit_version', self.call, 'app/version').decode().strip().lstrip('v')
        if not re.match(r'^[45]\.', version):
            raise PluginError('Supported qBittorrent versions: 4.x / 5.x.')
        fields = {'savepath': path, 'category': category, 'autoTMM': 'false',
                  ('stopped' if version.startswith('5.') else 'paused'): str(paused).lower()}
        boundary = 'Hermes' + secrets.token_hex(20)
        chunks = []
        for key, value in fields.items():
            chunks.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, key, value)).encode())
        chunks += [('--%s\r\nContent-Disposition: form-data; name="torrents"; filename="resource.torrent"\r\nContent-Type: application/x-bittorrent\r\n\r\n' % boundary).encode(), torrent, ('\r\n--%s--\r\n' % boundary).encode()]
        if stage_call('qbit_add', self.call, 'torrents/add', b''.join(chunks), 'multipart/form-data; boundary=' + boundary).strip() != b'Ok.':
            raise PluginError('qBittorrent did not acknowledge submission; check status before retrying.')


def save_path(value):
    """Validate qBittorrent's POSIX path, not a path on the Hermes host."""
    if (not isinstance(value, str) or not value.startswith('/') or value.startswith('//')
            or len(value) > 4096 or '..' in PurePosixPath(value).parts
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise PluginError('Save path must be an absolute qBittorrent POSIX path without .. or control characters.')
    return str(PurePosixPath(value))


def configured_default(cfg):
    # v0.2 migration: use the first saved alias until the administrator saves the new form.
    legacy = cfg.get('destinations', {})
    return save_path(cfg.get('default_save_path', next(iter(legacy.values()), '/downloads')))


class App:
    def __init__(self, cfg, mt=None, qb=None):
        self.cfg = cfg
        self.default_save_path = configured_default(cfg)
        self.mt = mt or MTeam(cfg['mteam'], HTTP(cookies=False))
        self.qb = qb or QBit(cfg['qbittorrent'], HTTP())
        state = Path(cfg.get('state_path', '~/.hermes/mteam-qbit/state.sqlite3')).expanduser()
        state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(state, timeout=10)
        os.chmod(state, 0o600)
        self.db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, rid TEXT, title TEXT, destination TEXT, path TEXT, created REAL, state TEXT, hash TEXT, paused INTEGER, category TEXT)')
        self.db.commit()

    def list_tasks(self, page=1, limit=20):
        # Validate before even authenticating; this operation never submits or deletes tasks.
        if type(page) is not int or not 1 <= page <= 1000 or type(limit) is not int or not 1 <= limit <= 100:
            raise PluginError('page must be 1–1000 and limit must be 1–100.')
        self.qb.login()
        return self.qb.list_tasks(page, limit)

    def delete_task(self, digest, user_confirmed=False, delete_files=False, delete_files_confirmed=False):
        if isinstance(digest, str): digest = digest.lower()
        if not isinstance(digest, str) or not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', digest):
            raise PluginError('One exact torrent hash is required; all and multiple hashes are forbidden.')
        if user_confirmed is not True:
            raise PluginError('Explicit user confirmation of this exact task is required.')
        if type(delete_files) is not bool:
            raise PluginError('delete_files must be boolean.')
        if delete_files and delete_files_confirmed is not True:
            raise PluginError('Deleting files may be irreversible; explicit confirmation of permanent file deletion is required.')
        self.qb.login()
        tasks = self.qb.info(digest)
        matches = [task for task in tasks if isinstance(task, dict) and task.get('hash') == digest]
        if len(matches) != 1:
            return {'state': 'not_deleted', 'hash': digest, 'note': 'Expected exactly one matching task; no deletion sent.'}
        try:
            self.qb.delete(digest, delete_files)
        except Exception as error:
            raise StageError('qbit_delete', getattr(error, 'http_status', None), uncertain=True,
                             reason=getattr(error, 'reason', 'unexpected_error')) from None
        # Keep existing submission reservations: deleting is not permission to automatically resubmit.
        try:
            remaining = self.qb.info(digest)
        except Exception as error:
            raise StageError('qbit_delete_verification', getattr(error, 'http_status', None), uncertain=True,
                             reason=getattr(error, 'reason', 'unexpected_error')) from None
        remaining = [task for task in remaining if isinstance(task, dict) and task.get('hash') == digest]
        return {'state': 'deleted' if not remaining else 'deletion_pending', 'hash': digest,
                'delete_files_requested': delete_files,
                'note': 'Task absence does not verify physical file erasure. Do not automatically retry deletion.'}

    def prepare(self, rid, path=None):
        resolved = self.default_save_path if path is None else save_path(path)
        source = 'default-v3' if path is None else 'explicit-v3'
        details = self.mt.details(resource_id(rid))
        request_id = secrets.token_hex(16)
        paused = bool(self.cfg.get('start_paused', False))
        category = self.cfg.get('category', 'hermes')
        if not isinstance(category, str) or any(c in category for c in '\r\n\0'):
            raise PluginError('Invalid category.')
        self.db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (request_id, str(rid), details['title'], source, resolved, time.time(), 'prepared', None, paused, category))
        self.db.commit()
        return {'request_id': request_id, 'resource': details, 'save_path': resolved,
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
            if not row['hash']:
                tasks = self.qb.info_tag('mteam-qbit-' + request_id)
                if tasks:
                    return {'state': 'present', 'tasks': tasks, 'note': 'No repeat submission.'}
            return {'state': 'unknown', 'hash': row['hash'], 'note': 'Not visible yet. Will not resubmit automatically; inspect qBittorrent.'}
        if row['state'] != 'approved':
            raise PluginError('User confirmation is required; prepare alone does not authorize submission.')
        if time.time() - row['created'] > self.cfg.get('approval_ttl_seconds', 900):
            raise PluginError('Approval expired; prepare again.')
        if row['destination'] not in ('default-v3', 'explicit-v3'):
            raise PluginError('Legacy prepared request; prepare again with a save path.')
        save_path(row['path'])
        # Serialize resource reservation across connections before generating the ephemeral URL.
        # A pending/acknowledged URL cannot be safely resent just because no task is visible yet.
        self.db.execute('BEGIN IMMEDIATE')
        previous = self.db.execute("SELECT id FROM requests WHERE rid=? AND id!=? AND state IN ('submitting','submitted','unknown') LIMIT 1",
                                   (row['rid'], request_id)).fetchone()
        if previous:
            self.db.rollback()
            return {'state': 'resource_already_submitted', 'request_id': previous['id'],
                    'note': 'Use the original request to check status. No second submission was sent.'}
        count = self.db.execute("UPDATE requests SET state='submitting' WHERE id=? AND state='approved'", (request_id,)).rowcount
        self.db.commit()
        if not count:
            return {'state': 'busy', 'note': 'Another call already consumed approval.'}
        try:
            existing = self.qb.info_tag('mteam-qbit-r' + row['rid'])
            if existing:
                digest = existing[0].get('hash')
                digest = digest if isinstance(digest, str) and re.fullmatch('[a-f0-9]{40}', digest) else None
                self.db.execute("UPDATE requests SET state='submitted',hash=? WHERE id=?", (digest, request_id)); self.db.commit()
                return {'state': 'present', 'tasks': existing, 'note': 'Existing tagged task preserved; its save path was not changed.'}
            url = self.mt.download_url(row['rid'])
        except Exception:
            # No submission was attempted. A new preparation is safe; do not retain signed URL.
            self.db.execute("UPDATE requests SET state='failed_before_submission' WHERE id=?", (request_id,)); self.db.commit()
            raise
        try:
            self.qb.add_url(url, row['path'], row['category'], bool(row['paused']), request_id, row['rid'])
        except Exception as error:
            self.db.execute("UPDATE requests SET state='unknown' WHERE id=?", (request_id,)); self.db.commit()
            if isinstance(error, StageError):
                raise StageError(error.stage, error.http_status, uncertain=True, reason=error.reason) from None
            raise StageError('qbit_add', uncertain=True) from None
        finally:
            url = None
        self.db.execute("UPDATE requests SET state='submitted' WHERE id=?", (request_id,)); self.db.commit()
        try:
            tasks = self.qb.info_tag('mteam-qbit-' + request_id)
        except Exception as error:
            raise StageError('qbit_task_lookup', getattr(error, 'http_status', None), uncertain=True,
                             reason=getattr(error, 'reason', 'unexpected_error')) from None
        return {'state': 'present' if tasks else 'submitted_not_yet_visible', 'tasks': tasks,
                'note': 'URL accepted is not download completion. Check this request status; do not resubmit.'}

    def status(self, request_id):
        row = self.row(request_id)
        if row['state'] not in ('submitting', 'submitted', 'unknown') and not row['hash']:
            return {'state': row['state'], 'tasks': []}
        self.qb.login()
        tasks = self.qb.info(row['hash']) if row['hash'] else self.qb.info_tag('mteam-qbit-' + request_id)
        return {'state': 'present' if tasks else row['state'], 'hash': row['hash'], 'tasks': tasks}


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
