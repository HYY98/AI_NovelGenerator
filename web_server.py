import hashlib, json, mimetypes, os, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, unquote
import sys
from card_store import CardStore, CardStoreError, CARD_TYPES
from chapter_storage import ChapterStore, ChapterConflictError

MAX_BODY = 2 * 1024 * 1024
class NovelWebServer(ThreadingHTTPServer):
    def __init__(self, address=('127.0.0.1',8765), workspace=None):
        self.workspace = Path(workspace or 'workspace/Novel_Src').expanduser().resolve()
        self.project = self.workspace
        self.lock = threading.RLock()
        super().__init__(address, NovelHandler)

class NovelHandler(BaseHTTPRequestHandler):
    server_version = 'NovelGenerator/1.0'
    def _json(self, obj, status=200, headers=None):
        data=json.dumps(obj, ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(data))); [self.send_header(k,v) for k,v in (headers or {}).items()]; self.end_headers(); self.wfile.write(data)
    def _body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n>MAX_BODY: raise ValueError('request body too large')
        return json.loads(self.rfile.read(n) or b'{}')
    def _guard(self, mutation=False):
        host=self.headers.get('Host','').split(':')[0]
        if host not in ('127.0.0.1','localhost'): raise PermissionError('invalid host')
        if mutation:
            if self.headers.get('X-Novel-Request')!='1': raise PermissionError('missing request header')
            origin=self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'): raise PermissionError('invalid origin')
            p=self.headers.get('X-Novel-Project')
            if p and Path(p).expanduser().resolve()!=self.server.project: raise PermissionError('project changed')
    def _stores(self): return ChapterStore(self.server.project), CardStore(self.server.project)
    def _dispatch(self, method, path):
        if path=='/api/health': return self._json({'ok':True})
        if not path.startswith('/api/'):
            rel = unquote(path.lstrip('/')) or 'index.html'
            target = (Path(__file__).parent / 'web' / rel).resolve()
            webroot = (Path(__file__).parent / 'web').resolve()
            if target != webroot and webroot not in target.parents:
                raise PermissionError('invalid static path')
            if not target.is_file() and rel == 'index.html': target = webroot / 'index.html'
            if not target.is_file(): raise FileNotFoundError()
            data = target.read_bytes(); self.send_response(200); self.send_header('Content-Type', mimetypes.guess_type(str(target))[0] or 'application/octet-stream'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
        if path=='/api/project' and method=='GET':
            c=CardStore(self.server.project); return self._json({'path':str(self.server.project),'cards':c.list_cards(),'chapters':[{'number':n} for n in ChapterStore(self.server.project).list_chapters()],'card_types':CARD_TYPES,'cards_revision':hashlib.sha256((c._revision or b'')).hexdigest()})
        if path=='/api/project' and method=='POST': self.server.project=Path(self._body()['path']).expanduser().resolve(); return self._dispatch('GET',path)
        cs, cards=self._stores()
        if path.startswith('/api/chapters/'):
            n=path.rsplit('/',1)[1]
            if method=='GET':
                text,b=cs.load(n); return self._json({'number':n,'text':text,'revision':hashlib.sha256(b).hexdigest()})
            body=self._body(); rev=bytes.fromhex(body.get('revision',''))
            if method=='PUT': return self._json({'number':n,'text':body['text'],'revision':hashlib.sha256(cs.save(n,body['text'],rev)).hexdigest()})
            if method=='DELETE': cs.delete(n,rev); return self._json({'ok':True})
        if path=='/api/chapters' and method=='POST':
            b=self._body(); n=cs.create(str(b['number']),b.get('text','')); return self._json({'number':n})
        if path=='/api/cards' and method=='POST':
            b=self._body(); self._check_card_rev(cards,b); return self._json({'card':cards.save_card(b['card'])})
        if path.startswith('/api/cards/') and method=='DELETE':
            b=self._body(); self._check_card_rev(cards,b); cards.delete_card(path.rsplit('/',1)[1]); return self._json({'ok':True})
        if path=='/api/cards/import' and method=='POST':
            b=self._body(); self._check_card_rev(cards,b); import tempfile
            fd,p=tempfile.mkstemp(); os.close(fd); Path(p).write_text(json.dumps(b['payload']),encoding='utf8'); count=cards.import_json(p,b.get('replace',False)); os.unlink(p); return self._json({'count':count})
        if path=='/api/cards/export' and method=='GET': return self._json({'version':1,'cards':cards.list_cards()},headers={'Content-Disposition':'attachment; filename="setting_cards.json"'})
        raise FileNotFoundError()
    def _check_card_rev(self,c,b):
        actual=hashlib.sha256((c._revision or b'')).hexdigest()
        if b.get('cards_revision')!=actual: raise ChapterConflictError('stale cards')
    def do_GET(self): self._run('GET',False)
    def do_POST(self): self._run('POST',True)
    def do_PUT(self): self._run('PUT',True)
    def do_DELETE(self): self._run('DELETE',True)
    def _run(self,m,mut):
        try:
            with self.server.lock: self._guard(mut); self._dispatch(m,urlparse(self.path).path)
        except PermissionError as e: self._json({'error':str(e)},403)
        except (FileNotFoundError,ValueError,KeyError,CardStoreError,ChapterConflictError) as e: self._json({'error':str(e)},409 if isinstance(e,ChapterConflictError) else 400)
        except Exception: self._json({'error':'internal server error'},500)
    def log_message(self,*a): pass

def serve(host='127.0.0.1', port=8765, workspace=None):
    s=NovelWebServer((host,port),workspace); s.serve_forever()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Local NovelGenerator Web editor')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--workspace', default=None)
    args = parser.parse_args()
    serve(args.host, args.port, args.workspace)
