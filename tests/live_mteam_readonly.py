"""Opt-in test2 integration probe. No production hosts, downloads or writes.

Run manually; excluded from unittest discovery. Credentials never appear in output.
"""
import argparse
import importlib.util
import json
import os
import stat
import time
import urllib.parse
from pathlib import Path

HOST = 'test2.m-team.cc'
PATHS = frozenset(('/api/member/profile', '/api/torrent/categoryList',
                   '/api/torrent/search', '/api/torrent/detail'))


def read_key(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Credential file must not be a symlink.')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd) as f:
        mode = os.fstat(f.fileno()).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError('Credential file must be a regular file with permissions 600 or stricter.')
        key = json.load(f)['MTEAM_TEST_API_KEY']
    if not isinstance(key, str) or not key or '\n' in key or '\r' in key:
        raise ValueError('Invalid test credential configuration.')
    return key


def allowed_request(method, url):
    u = urllib.parse.urlsplit(url)
    return (method == 'POST' and u.scheme == 'https' and u.netloc == HOST
            and not u.query and not u.fragment and u.path in PATHS)


def run(path, query='ubuntu', delay=3):
    spec = importlib.util.spec_from_file_location('mteam_live_core', Path(__file__).resolve().parents[1] / 'core.py')
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    key = read_key(path)
    core.secret = lambda _: key  # isolated import, process-local; never write environment/config

    class RestrictedHTTP(core.HTTP):
        def request(self, method, url, *args, **kwargs):
            if not allowed_request(method, url):
                raise ValueError('Live probe endpoint is not permitted.')
            return super().request(method, url, *args, **kwargs)

    mt = core.MTeam({'base_url': 'https://' + HOST, 'user_agent': 'MTeamQBit/1.0'},
                    RestrictedHTTP(cookies=False))
    state = {}
    results = []

    def probe(name, fn):
        try:
            result, summary = fn()
            state[name] = result
            checks = ('returned_categories_match', 'category_id_selected')
            passed = all(summary.get(k, True) is True for k in checks)
            record = {'operation': name, 'ok': passed, **summary}
            if not passed:
                record['error_type'] = 'VerificationMismatch'
        except Exception as e:
            # Do not print raw exceptions, headers, messages or account data.
            record = {'operation': name, 'ok': False, 'error_type': type(e).__name__}
            status = getattr(e, 'status', getattr(e, 'http_status', None))
            if type(status) is int and 100 <= status <= 599:
                record['http_status'] = status
        results.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
        time.sleep(delay)

    probe('profile', lambda: (mt.test_connection(), {'authenticated': True}))
    def categories():
        r = mt.categories()
        return r, {'category_count': len(r['categories']), 'group_count': len(r['groups'])}
    probe('categories', categories)
    for page in (1, 2):
        def search(page=page):
            r = mt.search(query, page, 5)
            return r, {'count': len(r['items']), 'page': r['page'],
                       'requested_limit': r['requested_limit'], 'limit': r['limit'],
                       'has_more': r['has_more'], 'next_page': r['next_page']}
        probe('search_page_' + str(page), search)
    first = state.get('search_page_1', {}).get('items', [])
    if first:
        rid = first[0]['id']
        probe('details', lambda: (mt.details(rid), {'resource_id': rid}))
        category = first[0].get('category_id')
        if category:
            def filtered():
                r = mt.search(query, 1, 5, category_ids=[category])
                return r, {'count': len(r['items']), 'category_id': category,
                           'returned_categories_match': all(x.get('category_id') == category for x in r['items'])}
            probe('category_id_search', filtered)
            rows = state.get('categories', {}).get('categories', [])
            row = next((x for x in rows if x['id'] == category), None)
            if row:
                name = next((row.get(k) for k in ('nameChs', 'nameCht', 'nameEng') if row.get(k)), None)
                if name:
                    def named():
                        r = mt.search(query, 1, 5, category=name)
                        return r, {'count': len(r['items']), 'category_id_selected': category in r['category_ids']}
                    probe('category_name_search', named)
    return results


def exit_status(results):
    return 0 if results and all(x.get('ok') is True for x in results) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', required=True, help='600 JSON file containing MTEAM_TEST_API_KEY; never pass a key on command line')
    parser.add_argument('--query', default='ubuntu')
    args = parser.parse_args()
    try:
        results = run(args.credentials, args.query)
        raise SystemExit(exit_status(results))
    except Exception as e:
        print(json.dumps({'ok': False, 'error_type': type(e).__name__, 'stage': 'probe_setup'}))
        raise SystemExit(1)
