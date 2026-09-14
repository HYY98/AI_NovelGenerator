"""Loopback-only HTTP adapter over the existing novel stores and task functions."""
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import threading
import tempfile
import contextlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent
if (ROOT / '.deps').is_dir():
    sys.path.insert(0, str(ROOT / '.deps'))
from card_store import CardStore, CardStoreError, CARD_TYPES, _decode
from chapter_storage import ChapterStore, ChapterConflictError
import web_config

MAX_BODY = 2 * 1024 * 1024
DOCUMENTS = {'architecture': 'Novel_architecture.txt', 'blueprint': 'Novel_directory.txt',
             'character': 'character_state.txt', 'summary': 'global_summary.txt', 'plot_arcs': 'plot_arcs.txt'}
from web_tasks import ACTIONS

class HTTPError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def safe_name(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 150:
        raise ValueError('名称无效')
    if any(c in value for c in '/\\:*?"<>|') or value in ('.', '..') or value.endswith('.') or any(ord(c) < 32 for c in value):
        raise ValueError('名称含非法路径字符')
    if value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(1,10)), *('LPT'+str(i) for i in range(1,10))}:
        raise ValueError('名称是系统保留名')
    return value


def safe_path(root, *parts):
    path = Path(root)
    if path.is_symlink():
        raise PermissionError('不允许符号链接')
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise PermissionError('不允许符号链接')
    return path


def read_optional(path):
    return path.read_bytes() if path.exists() else None


def text_payload(path):
    raw = read_optional(path)
    return {'text': (raw or b'').decode('utf-8-sig'), 'revision': web_config.digest(raw)}


def checked_revision(path, revision):
    raw = read_optional(path)
    if not isinstance(revision, str) or revision != web_config.digest(raw):
        raise web_config.ConfigConflict('文件已更新，请重新加载')
    return raw


class NovelWebServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address=('127.0.0.1', 17777), workspace=None, config_path=None, task_manager=None):
        if address[0] not in ('127.0.0.1', 'localhost'):
            raise ValueError('服务仅允许本机回环地址')
        self.config_path = Path(config_path) if config_path else ROOT / 'config.json'
        if workspace is None:
            try:
                cfg, _ = web_config.read_raw(self.config_path)
                workspace = cfg.get('other_params', {}).get('filepath') or ROOT / 'Novel_Src'
            except ValueError:
                workspace = ROOT / 'Novel_Src'  # Corrupt config remains intact and GET/config reports it.
        self.project = Path(workspace).expanduser().resolve()
        self.lock = threading.RLock()
        if task_manager is None:
            from web_tasks import TaskManager
            task_manager = TaskManager()
        self.tasks = task_manager
        self.task_ids = []
        super().__init__(address, NovelHandler)

    def active(self):
        if hasattr(self.tasks, 'has_active'):
            return self.tasks.has_active()
        return any((self.tasks.get(tid) or {}).get('status') in ('queued', 'running') for tid in self.task_ids)

    def server_close(self):
        super().server_close()
        self.tasks.shutdown()


class NovelHandler(BaseHTTPRequestHandler):
    server_version = 'NovelGenerator/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(35)

    def _respond(self, value, status=200, content_type='application/json; charset=utf-8', headers=None):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        if self.headers.get('Transfer-Encoding'):
            raise HTTPError(400, '不支持分块请求')
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise HTTPError(400, 'Content-Length 无效')
        if not 0 <= size <= MAX_BODY:
            raise HTTPError(413, '请求体超过 2MB')
        try:
            data = json.loads(self.rfile.read(size) or b'{}', parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError):
            raise HTTPError(400, 'JSON 无效')
        if not isinstance(data, dict):
            raise HTTPError(400, '请求体必须是对象')
        return data

    def _guard(self, path, mutation):
        allowed_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        host = self.headers.get('Host', '')
        if host not in allowed_hosts:
            raise HTTPError(403, 'Host 不允许')
        if path.startswith('/api/'):
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + host:
                raise HTTPError(403, 'Origin 不允许')
        if mutation:
            if self.headers.get('X-Novel-Request') != '1':
                raise HTTPError(403, '缺少本地请求标记')
            if path not in ('/api/project', '/api/config'):
                project = self.headers.get('X-Novel-Project')
                if not project:
                    raise HTTPError(403, '缺少项目路径标记')
                if Path(unquote(project)).expanduser().resolve() != self.server.project:
                    raise HTTPError(409, '项目已切换，请重新加载')
            if self.server.active():
                raise HTTPError(409, '任务运行中，请等待完成后再修改')

    def _project(self):
        cards = CardStore(self.server.project)
        return {'path': str(self.server.project), 'cards': cards.list_cards(),
                'chapters': [{'number': n} for n in ChapterStore(self.server.project).list_chapters()],
                'card_types': CARD_TYPES, 'cards_revision': web_config.digest(cards._revision)}

    def _config(self):
        safe, paths, rev = web_config.load(self.server.config_path)
        return {'config': safe, 'secret_paths': [list(p) for p in paths], 'revision': rev}

    def _text(self, body):
        text = body.get('text')
        if not isinstance(text, str):
            raise ValueError('text 必须是文本')
        return text

    def _static(self, path):
        rel = unquote(path.lstrip('/')) or 'index.html'
        if rel == 'icon.ico' or rel == 'favicon.ico':
            target = ROOT / 'icon.ico'
        else:
            target = (ROOT / 'web' / rel).resolve()
            if (ROOT / 'web').resolve() not in target.parents:
                raise HTTPError(403, '非法静态路径')
        if not target.is_file():
            raise FileNotFoundError()
        return self._respond(target.read_bytes(), content_type=mimetypes.guess_type(str(target))[0] or 'application/octet-stream')

    def _dispatch(self, method, path):
        if not path.startswith('/api/'):
            if method != 'GET': raise HTTPError(405, '方法不允许')
            return self._static(path)
        body = self._body() if method != 'GET' else {}
        if path == '/api/health' and method == 'GET':
            return self._respond({'ok': True})
        if path == '/api/project':
            if method == 'POST':
                value = body.get('path')
                if not isinstance(value, str) or not value.strip(): raise ValueError('项目路径不能为空')
                project = Path(value).expanduser().resolve()
                if project.exists() and not project.is_dir(): raise ValueError('项目路径必须是目录')
                CardStore(project)  # Validate before replacing the active project.
                old = self.server.project
                self.server.project = project
                try: result = self._project()
                except Exception:
                    self.server.project = old
                    raise
                return self._respond(result)
            if method == 'GET': return self._respond(self._project())
        if path == '/api/config':
            if method == 'PUT':
                web_config.update(self.server.config_path, body.get('config'), body.get('revision'), body.get('clear_secret_paths', []), body.get('copy_from'))
            if method in ('GET', 'PUT'): return self._respond(self._config())
        if path == '/api/folders' and method == 'GET':
            value = parse_qs(urlsplit(self.path).query).get('path', [str(self.server.project)])[0]
            root = Path(value).expanduser().resolve()
            if not root.is_dir(): raise FileNotFoundError()
            folders = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink())
            return self._respond({'path': str(root), 'parent': str(root.parent), 'folders': folders})
        if path == '/api/tasks' and method == 'POST':
            action, params = body.get('action'), body.get('params', {})
            if action not in ACTIONS or not isinstance(params, dict): raise ValueError('任务类型或参数无效')
            config, _ = web_config.read_raw(self.server.config_path)
            task = self.server.tasks.submit(action, str(self.server.project), config, params)
            self.server.task_ids.append(task['id'])
            self.server.task_ids = self.server.task_ids[-100:]
            return self._respond({'task': task}, 202)
        if path == '/api/tasks' and method == 'GET':
            tasks = [self.server.tasks.get(t) for t in self.server.task_ids]
            return self._respond({'tasks': [task for task in tasks if task is not None]})
        if path.startswith('/api/tasks/') and method == 'GET':
            task = self.server.tasks.get(unquote(path.rsplit('/', 1)[1]))
            if not task: raise FileNotFoundError()
            return self._respond({'task': task})
        if path.startswith(('/api/documents/', '/api/docs/')):
            key = path.rsplit('/', 1)[1]
            if key not in DOCUMENTS: raise FileNotFoundError()
            target = safe_path(self.server.project, DOCUMENTS[key])
            if method == 'PUT':
                raw = checked_revision(target, body.get('revision'))
                web_config.atomic_write(target, self._text(body).encode(), raw)
            if method in ('GET', 'PUT'): return self._respond({'key': key, **text_payload(target)})
        if path == '/api/chapters' and method == 'POST':
            n = body.get('number')
            if isinstance(n, bool) or not re.fullmatch('[0-9]+', str(n)) or int(n) <= 0:
                raise ValueError('章节编号必须是正整数')
            n = str(int(n))
            store = ChapterStore(self.server.project)
            safe_path(self.server.project, 'chapters', f'chapter_{n}.txt')
            store.create(n, self._text({'text': body.get('text', '')}))
            return self._respond({'number': n, **text_payload(store.path(n))}, 201)
        if path.startswith('/api/chapters/'):
            n = unquote(path.rsplit('/', 1)[1])
            store = ChapterStore(self.server.project)
            target = store.path(n)
            safe_path(self.server.project, 'chapters', target.name)
            if not target.is_file(): raise FileNotFoundError()
            if method in ('PUT', 'DELETE'):
                raw = checked_revision(target, body.get('revision'))
                if method == 'DELETE':
                    store.delete(n, raw)
                    return self._respond({'ok': True})
                store.save(n, self._text(body), raw)
            if method in ('GET', 'PUT'): return self._respond({'number': n, **text_payload(target)})
        if path.startswith('/api/cards'):
            safe_path(self.server.project, 'setting_cards.json')
            cards = CardStore(self.server.project)
            if method != 'GET' and body.get('cards_revision') != web_config.digest(cards._revision):
                raise web_config.ConfigConflict('卡片已更新，请重新加载')
            if path == '/api/cards' and method == 'POST':
                if not isinstance(body.get('card'), dict): raise ValueError('card 必须是对象')
                return self._respond({'card': cards.save_card(body['card'])})
            if path == '/api/cards/import' and method == 'POST':
                replace = body.get('replace', False)
                if type(replace) is not bool: raise ValueError('replace 必须是布尔值')
                incoming = _decode(json.dumps(body.get('payload'), ensure_ascii=False))
                cards._commit(incoming if replace else cards.list_cards() + incoming)
                return self._respond({'count': len(incoming)})
            if path == '/api/cards/export' and method == 'GET':
                return self._respond({'version': 1, 'cards': cards.list_cards()}, headers={'Content-Disposition': 'attachment; filename="setting_cards.json"'})
            if path.startswith('/api/cards/') and method == 'DELETE':
                cards.delete_card(unquote(path.rsplit('/', 1)[1]))
                return self._respond({'ok': True})
        if path == '/api/roles' and method == 'GET':
            root = safe_path(self.server.project, '角色库')
            roles = []
            if root.is_dir():
                for category in sorted(root.iterdir()):
                    if category.is_dir() and not category.is_symlink():
                        for file in sorted(category.glob('*.txt')):
                            if file.is_file() and not file.is_symlink():
                                roles.append({'category': category.name, 'name': file.stem, **text_payload(file)})
            return self._respond({'roles': roles})
        if path == '/api/roles' and method == 'POST':
            category, name = safe_name(body.get('category')), safe_name(body.get('name'))
            target = safe_path(self.server.project, '角色库', category, name + '.txt')
            raw = read_optional(target)
            if raw is not None or 'revision' in body:
                raw = checked_revision(target, body.get('revision'))
            web_config.atomic_write(target, self._text(body).encode(), raw)
            return self._respond({'role': {'category': category, 'name': name, **text_payload(target)}})
        if path.startswith('/api/roles/') and method in ('PUT', 'DELETE'):
            parts = path[len('/api/roles/'):].split('/')
            if len(parts) != 2: raise ValueError('角色路径无效')
            category, name = [safe_name(unquote(p)) for p in parts]
            target = safe_path(self.server.project, '角色库', category, name + '.txt')
            if not target.is_file(): raise FileNotFoundError()
            raw = checked_revision(target, body.get('revision'))
            if method == 'DELETE':
                target.unlink()
                return self._respond({'ok': True})
            web_config.atomic_write(target, self._text(body).encode(), raw)
            return self._respond({'role': {'category': category, 'name': name, **text_payload(target)}})
        if path.startswith('/api/webdav/') and method == 'POST':
            action = path.rsplit('/', 1)[1]
            if action not in ('test', 'backup', 'restore'): raise FileNotFoundError()
            if action != 'test' and body.get('confirm') is not True:
                raise ValueError('备份或恢复需要 confirm=true')
            config, _ = web_config.read_raw(self.server.config_path)
            dav = config.get('webdav_config', {})
            url = dav.get('webdav_url', '')
            if urlsplit(url).scheme not in ('http', 'https') or not urlsplit(url).netloc:
                raise ValueError('请配置 WebDAV URL')
            try:
                from webdav_client import WebDAVClient
                client = WebDAVClient(url, dav.get('webdav_username', ''), dav.get('webdav_password', ''))
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    if action == 'test': client.list_directory()
                    elif action == 'backup':
                        if not client.ensure_directory_exists('AI_Novel_Generator') or not client.upload_file(str(self.server.config_path), 'AI_Novel_Generator/config.json'):
                            raise RuntimeError()
                    else:
                        before = read_optional(self.server.config_path)
                        self.server.config_path.parent.mkdir(parents=True, exist_ok=True)
                        with tempfile.TemporaryDirectory(prefix='.webdav-', dir=self.server.config_path.parent) as temp:
                            target = Path(temp) / 'config.json'
                            if not client.download_file('AI_Novel_Generator/config.json', str(target)):
                                raise RuntimeError()
                            web_config.read_raw(target)
                            downloaded = target.read_bytes()
                            if before is not None:
                                client.backup(str(self.server.config_path))
                            web_config.atomic_write(self.server.config_path, downloaded, before)
            except Exception:
                raise HTTPError(502, 'WebDAV 操作失败，请检查连接与凭据')
            return self._respond({'ok': True})
        raise FileNotFoundError()

    def _run(self, method):
        try:
            with self.server.lock:
                path = urlsplit(self.path).path
                self._guard(path, method != 'GET')
                self._dispatch(method, path)
        except HTTPError as exc: self._respond({'error': exc.message}, exc.status)
        except (web_config.ConfigConflict, ChapterConflictError, FileExistsError): self._respond({'error': '内容已变化或已存在，请重新加载'}, 409)
        except FileNotFoundError: self._respond({'error': '文件或接口不存在'}, 404)
        except PermissionError: self._respond({'error': '路径访问被拒绝'}, 403)
        except (ValueError, TypeError, KeyError, UnicodeError): self._respond({'error': '参数或文件格式无效，请检查输入'}, 400)
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception: self._respond({'error': '服务器操作失败，未返回敏感详情'}, 500)

    def do_GET(self): self._run('GET')
    def do_POST(self): self._run('POST')
    def do_PUT(self): self._run('PUT')
    def do_DELETE(self): self._run('DELETE')
    def log_message(self, *args): pass


def serve(host='127.0.0.1', port=17777, workspace=None):
    server = NovelWebServer((host, port), workspace)
    print(f'NovelGenerator Web: http://{host}:{port}', flush=True)
    try: server.serve_forever()
    finally: server.server_close()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Local NovelGenerator Web')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=17777)
    parser.add_argument('--workspace')
    args = parser.parse_args()
    serve(args.host, args.port, args.workspace)
