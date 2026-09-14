"""Real HTTP task lifecycle with original-signature fake business functions."""
import json
from pathlib import Path
import tempfile
import threading
import time
import types
import sys
import unittest
from urllib.request import Request, urlopen
from urllib.parse import quote
from unittest.mock import patch
from config_manager import get_default_config
from test_web_original_contract import FUNCTIONS, signature
from web_server import NovelWebServer


class EndToEndTaskTests(unittest.TestCase):
    def test_original_workflow_through_http(self):
        fake = types.ModuleType('novel_generator')
        calls = []
        for name in FUNCTIONS:
            sig = signature(name)
            def invoke(*args, _sig=sig, _name=name, **kwargs):
                bound = _sig.bind(*args, **kwargs).arguments
                calls.append(_name)
                root = Path(bound.get('filepath', '.'))
                if _name == 'Novel_architecture_generate':
                    (root/'Novel_architecture.txt').write_text('测试架构',encoding='utf-8')
                elif _name == 'Chapter_blueprint_generate':
                    (root/'Novel_directory.txt').write_text('第1章 测试',encoding='utf-8')
                elif _name == 'build_chapter_prompt':
                    return '来自原函数接口的提示词'
                elif _name == 'generate_chapter_draft':
                    (root/'chapters').mkdir(exist_ok=True)
                    (root/'chapters/chapter_1.txt').write_text('测试正文',encoding='utf-8')
                    return '测试正文'
                elif _name == 'finalize_chapter':
                    (root/'global_summary.txt').write_text('测试摘要',encoding='utf-8')
                return None
            setattr(fake,name,invoke)
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules,{'novel_generator':fake}):
            root=Path(temp)
            cfg=get_default_config()
            for model in cfg['llm_configs'].values():model['api_key']='http-test-secret'
            config=root/'settings.json';config.write_text(json.dumps(cfg),encoding='utf-8')
            server=NovelWebServer(('127.0.0.1',0),workspace=root,config_path=config)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            def request(path,data=None):
                headers={'X-Novel-Request':'1','X-Novel-Project':quote(str(root)),'Content-Type':'application/json'}
                req=Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
                with urlopen(req,timeout=10) as response:return json.load(response)
            try:
                for action in ['architecture','blueprint','build_prompt','draft','finalize']:
                    params={'chapter_num':1,'word_number':100,'num_chapters':1,'topic':'测试','genre':'玄幻','chapter_text':'编辑后的正文','should_enrich':False}
                    if action=='draft':params['custom_prompt_text']='确认后的提示词'
                    task=request('/api/tasks',{'action':action,'params':params})['task']
                    deadline=time.monotonic()+5
                    while task['status'] in ('queued','running') and time.monotonic()<deadline:
                        task=request('/api/tasks/'+task['id'])['task']
                        if task['status'] in ('queued','running'):threading.Event().wait(.02)
                    self.assertEqual(task['status'],'completed',task)
                    self.assertNotIn('http-test-secret',json.dumps(task))
                self.assertEqual(request('/api/documents/summary')['text'],'测试摘要')
                self.assertEqual(request('/api/chapters/1')['text'],'编辑后的正文')
                self.assertIn('build_chapter_prompt',calls)
                self.assertIn('finalize_chapter',calls)
            finally:
                server.shutdown();server.server_close();thread.join(5)


if __name__=='__main__':unittest.main()
