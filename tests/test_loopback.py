"""Real loopback HTTPS transport against simulated M-Team/qBit, NOT live services."""
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.parse
import http.cookiejar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import HTTP, NoRedirect, App, MTeam, QBit, StageError, torrent_hash
TORRENT=b'd4:infod6:lengthi1e4:name1:x6:pieces20:abcdefghijklmnopqrstee'

class LoopbackTests(unittest.TestCase):
    def test_real_https_simulated_services_and_fetch_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            cert,key=Path(tmp)/'cert.pem',Path(tmp)/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
                '-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost',
                '-addext','subjectAltName=DNS:localhost'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            seen=[]; state={'added':False,'fetch':'ok','adds':0,'tags':''}
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_GET(self):self.handle_request()
                def do_POST(self):self.handle_request()
                def handle_request(self):
                    body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                    path=self.path.split('?')[0]
                    seen.append((path,self.headers.get('User-Agent'),self.headers.get('x-api-key'),self.headers.get('Cookie')))
                    payload=b'';status=200;headers={'Set-Cookie':'SITE=must-not-leak; Path=/'} if path.startswith('/api/torrent/') else {}
                    if path=='/api/torrent/detail':payload=json.dumps({'code':0,'data':{'id':'1','name':'synthetic fixture'}}).encode()
                    elif path=='/api/torrent/genDlToken':payload=b'{"code":0,"data":"https://isolated.test/torrent?token=FAKE"}'
                    elif path=='/torrent':
                        self.server.fetches+=1
                        if state['fetch']=='redirect':status=302;headers['Location']='https://forbidden.invalid/?token=FAKE'
                        elif state['fetch']=='forbidden':status=403
                        else:payload=TORRENT
                    elif path=='/api/v2/auth/login':payload=b'Ok.';headers['Set-Cookie']='SID=synthetic; Path=/'
                    elif path=='/api/v2/app/version':payload=b'v5.1.0'
                    elif path=='/api/v2/torrents/info':
                        payload=json.dumps([{'hash':torrent_hash(TORRENT),'name':'synthetic','save_path':'/test/path','tags':state['tags']}] if state['added'] else []).encode()
                    elif path=='/api/v2/torrents/add':
                        form=urllib.parse.parse_qs(body.decode())
                        if form.get('urls')!=['https://isolated.test/torrent?token=FAKE'] or form.get('savepath')!=['/test/path'] or form.get('stopped')!=['false']:status=400
                        else:state['adds']+=1;state['added']=True;state['tags']=form['tags'][0];payload=b'Ok.'
                    else:status=404
                    self.send_response(status)
                    for k,v in headers.items():self.send_header(k,v)
                    self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.fetches=0
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
            server.socket=context.wrap_socket(server.socket,server_side=True)
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            base='https://localhost:'+str(server.server_port)
            class LocalHTTP(HTTP):
                def __init__(self,cookies=True):
                    super().__init__(cookies=cookies)
                    # Test-only local CA, TLS verification remains enabled.
                    handlers=[urllib.request.ProxyHandler({}),NoRedirect(),
                        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(cert)))]
                    if cookies: handlers.append(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                    self.opener=urllib.request.build_opener(*handlers)
                def request(self,method,url,*args,**kwargs):
                    # Test-only routing of fake signed URL to our ephemeral HTTPS listener.
                    if url.startswith('https://isolated.test/'):url=base+url[len('https://isolated.test'):]
                    assert url.startswith(base+'/'), 'No external network allowed'
                    return super().request(method,url,*args,**kwargs)
            cfg={'default_save_path':'/test/path','state_path':tmp+'/state.db'}
            app=App(cfg,MTeam({'base_url':base,'download_hosts':['isolated.test'],'user_agent':'Loopback-UA'},LocalHTTP(cookies=False)),
                    QBit({'base_url':base},LocalHTTP()))
            try:
                with patch.dict(os.environ,{'MTEAM_API_KEY':'fake-key','QBIT_USERNAME':'fake','QBIT_PASSWORD':'fake'}):
                    request=app.prepare('1')
                    result=app.submit(request['request_id'],True)
                    self.assertEqual(result['state'],'present');self.assertEqual(state['adds'],1)
                    self.assertEqual(server.fetches,0)  # URL is delegated; Hermes performs no torrent GET.
                    self.assertNotIn('token=FAKE',Path(tmp+'/state.db').read_bytes().decode(errors='ignore'))
                    app.submit(request['request_id'])
                    self.assertEqual(state['adds'],1)
                    for mode,reason,status in [('redirect','redirect_refused',302),('forbidden','http_error',403)]:
                        state['fetch']=mode
                        with self.assertRaises(StageError) as error:app.mt.torrent('1')
                        self.assertEqual(error.exception.reason,reason)
                        self.assertEqual(error.exception.http_status,status)
                        self.assertNotIn('FAKE',str(error.exception))
                    self.assertTrue(all(ua=='Loopback-UA' for path,ua,keyval,cookie in seen if path.startswith('/api/torrent/') or path=='/torrent'))
                    self.assertTrue(all(keyval is None and cookie is None for path,ua,keyval,cookie in seen if path=='/torrent'))
                    self.assertTrue(all(cookie is None for path,ua,keyval,cookie in seen if path.startswith('/api/torrent/')))
                    self.assertTrue(any(cookie=='SID=synthetic' for path,ua,keyval,cookie in seen if path=='/api/v2/torrents/add'))
            finally:
                app.db.close();server.shutdown();server.server_close();worker.join(timeout=2)
