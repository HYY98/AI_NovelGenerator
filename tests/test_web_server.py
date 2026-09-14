"""Offline HTTP integration tests; no real configuration or API network calls."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import quote
from unittest.mock import patch
from config_manager import get_default_config
from web_server import NovelWebServer
import web_config

class FakeTasks:
    def __init__(self): self.task = None; self.received = None
    def submit(self, *args):
        self.received = args
        self.task = {'id': 'fake', 'status': 'running', 'logs': [], 'result': None}
        return dict(self.task)
    def get(self, tid): return self.task if tid == 'fake' else None
    def shutdown(self): pass

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / '小说'
        self.config = self.root / 'global.json'
        self.tasks = FakeTasks()
        self.server = NovelWebServer(('127.0.0.1', 0), self.project, config_path=self.config, task_manager=self.tasks)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()
    def request(self, method, path, body=None, headers=None):
        h = {'X-Novel-Request': '1', 'X-Novel-Project': quote(str(self.server.project), safe='')}
        h.update(headers or {})
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        conn.request(method, path, payload, h)
        r = conn.getresponse(); data = r.read(); status = r.status; conn.close()
        try: data = json.loads(data)
        except ValueError: pass
        return status, data
    def test_static_health_security(self):
        self.assertEqual(self.request('GET','/')[0], 200)
        self.assertEqual(self.request('GET','/favicon.ico')[0], 200)
        self.assertEqual(self.request('GET','/api/health')[1], {'ok':True})
        self.assertEqual(self.request('GET','/api/config',headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('GET','/api/project',headers={'Host':'127.0.0.1:1'})[0],403)
        self.assertEqual(self.request('POST','/api/project',{'path':str(self.project)}, {'X-Novel-Request':''})[0],403)
        self.assertEqual(self.request('GET','/%2e%2e/config.json')[0],403)
        self.assertEqual(self.request('GET','/api/missing')[0],404)
    def test_chapter_roundtrip_and_conflict(self):
        code, chapter = self.request('POST','/api/chapters',{'number':1,'text':' a\r\n'})
        self.assertEqual(code,201); self.assertEqual(chapter['number'],'1')
        code, saved = self.request('PUT','/api/chapters/1', {'text':' new \n','revision':chapter['revision']})
        self.assertEqual(code,200); self.assertEqual(saved['text'],' new \n')
        self.assertEqual(self.request('PUT','/api/chapters/1',{'text':'bad','revision':chapter['revision']})[0],409)
        self.assertEqual(self.request('DELETE','/api/chapters/1', {'revision':saved['revision']})[0],200)
        self.assertEqual(self.request('GET','/api/chapters/1')[0],404)
        for number in (0,-1,True,'../bad'):
            self.assertEqual(self.request('POST','/api/chapters',{'number':number})[0],400)
    def test_project_isolation_and_invalid_switch(self):
        self.assertEqual(self.request('POST','/api/chapters', {'number':1}, {'X-Novel-Project':''})[0],403)
        self.assertEqual(self.request('POST','/api/chapters', {'number':1}, {'X-Novel-Project':quote(str(self.root/'other'))})[0],409)
        invalid = self.root/'file'; invalid.write_text('x')
        self.assertEqual(self.request('POST','/api/project',{'path':str(invalid)})[0],400)
        self.assertEqual(self.server.project,self.project)
        self.assertEqual(self.request('POST','/api/project',{'path':str(self.root/'new')})[0],200)
    def test_cards_import_export_stale_and_transaction(self):
        rev = self.request('GET','/api/project')[1]['cards_revision']
        card = {'id':'one','type':'location','name':'城','fields':{},'enabled':True}
        self.assertEqual(self.request('POST','/api/cards',{'card':card,'cards_revision':rev})[0],200)
        self.assertEqual(self.request('POST','/api/cards',{'card':card,'cards_revision':rev})[0],409)
        exported = self.request('GET','/api/cards/export')[1]
        rev = self.request('GET','/api/project')[1]['cards_revision']
        self.assertEqual(self.request('POST','/api/cards/import',{'payload':exported,'replace':False,'cards_revision':rev})[0],400)
        self.assertEqual(len(self.request('GET','/api/cards/export')[1]['cards']),1)
        self.assertEqual(self.request('POST','/api/cards/import',{'payload':exported,'replace':True,'cards_revision':rev})[0],200)
        rev = self.request('GET','/api/project')[1]['cards_revision']
        self.assertEqual(self.request('DELETE','/api/cards/one',{'cards_revision':rev})[0],200)
    def test_docs_and_roles(self):
        doc = self.request('GET','/api/documents/architecture')[1]
        self.assertEqual(self.request('PUT','/api/documents/architecture',{'text':'设定','revision':doc['revision']})[0],200)
        self.assertEqual(self.request('PUT','/api/documents/architecture',{'text':'旧','revision':doc['revision']})[0],409)
        self.assertEqual(self.request('GET','/api/documents/config')[0],404)
        code, result = self.request('POST','/api/roles',{'category':'主角','name':'张三','text':'人物'})
        self.assertEqual(code,200); role = result['role']
        url='/api/roles/'+quote('主角')+'/'+quote('张三')
        self.assertEqual(self.request('PUT',url,{'text':'更新','revision':role['revision']})[0],200)
        self.assertEqual(self.request('DELETE',url,{'revision':role['revision']})[0],409)
        roles=self.request('GET','/api/roles')[1]['roles']
        self.assertEqual(self.request('DELETE',url,{'revision':roles[0]['revision']})[0],200)
        self.assertEqual(self.request('POST','/api/roles',{'category':'../escape','name':'x','text':''})[0],400)
    def test_global_config_mask_preserve_rename_and_clear(self):
        cfg=get_default_config(); name=next(iter(cfg['llm_configs']))
        cfg['llm_configs'][name]['api_key']='secret-value'; cfg['llm_configs'][name]['id']='stable'
        self.config.write_text(json.dumps(cfg), encoding='utf-8')
        response=self.request('GET','/api/config')[1]
        self.assertNotIn('secret-value',json.dumps(response))
        safe=response['config']; safe['llm_configs']['renamed']=safe['llm_configs'].pop(name)
        for key,value in safe['choose_configs'].items():
            if value==name:safe['choose_configs'][key]='renamed'
        body={'config':safe,'revision':response['revision']}
        code,res=self.request('PUT','/api/config',body)
        self.assertEqual(code,200)
        self.assertEqual(web_config.read_raw(self.config)[0]['llm_configs']['renamed']['api_key'],'secret-value')
        self.assertFalse((self.project/'config.json').exists())
        self.assertEqual(self.request('PUT','/api/config',body)[0],409)
        body={'config':res['config'],'revision':res['revision'],'clear_secret_paths':[['llm_configs','renamed','api_key']]}
        self.assertEqual(self.request('PUT','/api/config',body)[0],200)
        self.assertEqual(web_config.read_raw(self.config)[0]['llm_configs']['renamed']['api_key'],'')
    def test_missing_and_corrupt_config(self):
        response=self.request('GET','/api/config')[1]
        self.assertEqual(self.request('PUT','/api/config',{'config':response['config'],'revision':response['revision']})[0],200)
        self.config.write_text('{broken')
        self.assertEqual(self.request('GET','/api/config')[0],400)
        self.assertEqual(self.request('PUT','/api/config',{'config':get_default_config(),'revision':response['revision']})[0],400)
        self.assertEqual(self.config.read_text(),'{broken')
    def test_tasks_snapshot_and_running_write_lock(self):
        cfg=get_default_config(); cfg['webdav_config']['webdav_password']='hidden'
        self.config.write_text(json.dumps(cfg))
        self.assertEqual(self.request('POST','/api/tasks',{'action':'unknown'})[0],400)
        code,result=self.request('POST','/api/tasks',{'action':'architecture','params':{'topic':'小说'}})
        self.assertEqual(code,202);self.assertEqual(result['task']['id'],'fake')
        self.assertEqual(self.tasks.received[2]['webdav_config']['webdav_password'],'hidden')
        self.assertEqual(self.request('GET','/api/tasks/fake')[0],200)
        self.assertEqual(self.request('POST','/api/project',{'path':str(self.root/'other')})[0],409)
        self.assertEqual(self.request('PUT','/api/config',{})[0],409)
        self.assertEqual(self.request('POST','/api/chapters',{'number':1})[0],409)
        self.tasks.task['status']='completed'
        self.assertEqual(self.request('POST','/api/chapters',{'number':1})[0],201)
    def test_webdav_confirmation_and_redaction(self):
        cfg=get_default_config();cfg['webdav_config'].update(webdav_url='https://dav.example',webdav_password='private')
        self.config.write_text(json.dumps(cfg))
        self.assertEqual(self.request('POST','/api/webdav/backup',{})[0],400)
        with patch('webdav_client.WebDAVClient') as factory:
            factory.return_value.list_directory.side_effect=RuntimeError('private password')
            code,result=self.request('POST','/api/webdav/test',{})
            self.assertEqual(code,502);self.assertNotIn('private',str(result))
            factory.return_value.ensure_directory_exists.return_value=True
            factory.return_value.upload_file.return_value=True
            self.assertEqual(self.request('POST','/api/webdav/backup',{'confirm':True})[0],200)
            factory.return_value.upload_file.assert_called_with(str(self.config),'AI_Novel_Generator/config.json')
    def test_restore_validates_before_replacing(self):
        cfg=get_default_config();cfg['webdav_config']['webdav_url']='https://dav.example'
        self.config.write_text(json.dumps(cfg)); before=self.config.read_bytes()
        def invalid_download(remote, target):
            Path(target).write_text('{"llm_configs":[],"embedding_configs":{},"choose_configs":{},"other_params":{}}')
            return True
        with patch('webdav_client.WebDAVClient') as factory:
            factory.return_value.download_file.side_effect=invalid_download
            self.assertEqual(self.request('POST','/api/webdav/restore',{'confirm':True})[0],502)
        self.assertEqual(self.config.read_bytes(),before)
        self.assertFalse(list(self.root.glob('.webdav-*')))

    def test_list_secret_masking(self):
        cfg=get_default_config();cfg['extra']=[{'token':'nested-secret'}]
        self.config.write_text(json.dumps(cfg))
        result=self.request('GET','/api/config')[1]
        self.assertNotIn('nested-secret',json.dumps(result))
        self.assertEqual(self.request('PUT','/api/config',{'config':result['config'],'revision':result['revision']})[0],200)
        self.assertEqual(web_config.read_raw(self.config)[0]['extra'][0]['token'],'nested-secret')

    def test_task_history_bounded_and_evicted_filtered(self):
        self.server.task_ids = ['old-' + str(i) for i in range(100)]
        code, result = self.request('POST', '/api/tasks', {'action': 'architecture', 'params': {}})
        self.assertEqual(code, 202)
        self.assertEqual(len(self.server.task_ids), 100)
        self.assertNotIn('old-0', self.server.task_ids)
        tasks = self.request('GET', '/api/tasks')[1]['tasks']
        self.assertEqual([task['id'] for task in tasks], ['fake'])

    def test_oversized_body(self):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        conn.request('POST','/api/project',body=None,headers={'X-Novel-Request':'1','Content-Length':str(2*1024*1024+1)})
        response=conn.getresponse()
        self.assertEqual(response.status,413)
        response.read();conn.close()

if __name__ == '__main__': unittest.main()
