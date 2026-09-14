"""Offline tests of original-pipeline dispatch and task isolation."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
from config_manager import get_default_config
import web_tasks


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = get_default_config()
        self.params = {'topic': 'Novel', 'num_chapters': 3, 'chapter_num': 1, 'word_number': 100,
                       'chapter_text': 'body', 'should_enrich': False}
        self.fake = types.ModuleType('novel_generator')
        for name in ('Novel_architecture_generate', 'Chapter_blueprint_generate', 'build_chapter_prompt',
                     'generate_chapter_draft', 'finalize_chapter', 'enrich_chapter_text',
                     'import_knowledge_file', 'clear_vector_store'):
            setattr(self.fake, name, Mock(return_value='text'))
        self.checker = types.ModuleType('consistency_checker')
        self.checker.check_consistency = Mock(return_value='review')
        self.modules = patch.dict(sys.modules, {'novel_generator': self.fake, 'consistency_checker': self.checker})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def call(self, action, **extra):
        return web_tasks.dispatch(action, self.root, self.cfg, dict(self.params, **extra))

    def test_finalize_false_and_atomic_save_before_original(self):
        self.fake.finalize_chapter.side_effect = lambda **kw: self.assertEqual((self.root/'chapters/chapter_1.txt').read_text(), 'body')
        result = self.call('finalize', filepath='must-not-use')
        self.assertEqual(result['text'], 'body')
        self.fake.enrich_chapter_text.assert_not_called()
        self.assertEqual(self.fake.finalize_chapter.call_args.kwargs['filepath'], str(self.root))

    def test_finalize_stale_revision_preserves_disk_and_skips_model(self):
        path = self.root/'chapters/chapter_1.txt'
        path.parent.mkdir()
        path.write_text('external change')
        with self.assertRaises(ValueError):
            self.call('finalize', chapter_revision='stale', should_enrich=True)
        self.assertEqual(path.read_text(), 'external change')
        self.fake.enrich_chapter_text.assert_not_called()
        self.fake.finalize_chapter.assert_not_called()

    def test_empty_or_missing_generated_documents_fail(self):
        for action, filename in [('architecture','Novel_architecture.txt'), ('blueprint','Novel_directory.txt')]:
            with self.subTest(action=action):
                with self.assertRaises(RuntimeError):
                    self.call(action)
                (self.root/filename).write_text('  ')
                with self.assertRaises(RuntimeError):
                    self.call(action)

    def test_enrichment_true_uses_final_model(self):
        self.fake.enrich_chapter_text.return_value = 'expanded'
        self.assertEqual(self.call('finalize', should_enrich=True)['text'], 'expanded')
        self.assertEqual(self.fake.enrich_chapter_text.call_args.kwargs['model_name'], self.cfg['llm_configs'][self.cfg['choose_configs']['final_chapter_llm']]['model_name'])

    def test_prompt_roles_and_custom_prompt(self):
        lib = self.root / '角色库' / '主角'
        lib.mkdir(parents=True)
        (lib/'Alice.txt').write_text('role description', encoding='utf-8')
        result = self.call('build_prompt', characters_involved='Alice')
        self.assertIn('role description', result['text'])
        self.call('draft', custom_prompt_text='exact prompt', characters_involved='Alice', api_key='frontend cannot override')
        self.assertEqual(self.fake.generate_chapter_draft.call_args.kwargs['custom_prompt_text'], 'exact prompt')
        self.assertEqual(self.fake.generate_chapter_draft.call_args.kwargs['api_key'], '')

    def test_consistency_reads_disk_not_frontend_setting(self):
        (self.root/'Novel_architecture.txt').write_text('setting', encoding='utf-8')
        (self.root/'plot_arcs.txt').write_text('arcs', encoding='utf-8')
        self.call('consistency', novel_setting='injected', plot_arcs='injected')
        kwargs = self.checker.check_consistency.call_args.kwargs
        self.assertIn('setting', kwargs['novel_setting'])
        self.assertEqual(kwargs['plot_arcs'], 'arcs')
        self.assertEqual(self.call('plot_arcs')['text'], 'arcs')

    def test_batch_calls_draft_and_finalize_per_chapter(self):
        result = self.call('batch', start_chapter=2, end_chapter=3, auto_enrich=False, chapter_revision='irrelevant-single-chapter-revision')
        self.assertEqual(result['chapters'], [2, 3])
        self.assertEqual(self.fake.generate_chapter_draft.call_count, 2)
        self.assertEqual(self.fake.finalize_chapter.call_count, 2)
        self.assertEqual([c.kwargs['novel_number'] for c in self.fake.finalize_chapter.call_args_list], [2, 3])

    def test_knowledge_text_lifetime_and_local_file(self):
        paths = []
        def inspect(**kwargs):
            path = Path(kwargs['file_path'])
            self.assertEqual(path.read_text(encoding='utf-8'), 'knowledge')
            paths.append(path)
        self.fake.import_knowledge_file.side_effect = inspect
        self.call('knowledge_import', text='knowledge')
        self.assertFalse(paths[0].exists())
        source = self.root/'source.txt'
        source.write_text('knowledge')
        self.call('knowledge_import', file_path=str(source))
        self.assertTrue(source.exists())

    def test_clear_confirmation_and_failed_original(self):
        with self.assertRaises(ValueError):
            self.call('clear_vectorstore')
        self.fake.clear_vector_store.return_value = False
        with self.assertRaises(RuntimeError):
            self.call('clear_vectorstore', confirm=True)
        self.fake.clear_vector_store.return_value = True
        self.assertTrue(self.call('clear_vectorstore', confirm=True)['success'])

    def test_proxy_english_restored_after_failure(self):
        import config_manager
        import prompt_definitions
        before = prompt_definitions.first_chapter_draft_prompt
        flag = config_manager.IS_ENGLISH
        self.cfg['proxy_setting'] = {'enabled': True, 'proxy_url': '127.0.0.1', 'proxy_port': '9999'}
        def fail(**kwargs):
            self.assertTrue(config_manager.IS_ENGLISH)
            self.assertEqual(os.environ['HTTPS_PROXY'], 'http://127.0.0.1:9999')
            raise RuntimeError('failure')
        self.fake.build_chapter_prompt.side_effect = fail
        with patch.dict(os.environ, {'HTTPS_PROXY': 'original'}):
            with self.assertRaises(RuntimeError):
                self.call('build_prompt', english_mode=True)
            self.assertEqual(os.environ['HTTPS_PROXY'], 'original')
        self.assertEqual(config_manager.IS_ENGLISH, flag)
        self.assertEqual(prompt_definitions.first_chapter_draft_prompt, before)

    def test_lazy_connection_adapters(self):
        llm = types.ModuleType('llm_adapters')
        llm.create_llm_adapter = Mock(return_value=Mock(invoke=Mock(return_value='OK')))
        emb = types.ModuleType('embedding_adapters')
        emb.create_embedding_adapter = Mock(return_value=Mock(embed_query=Mock(return_value=[1,2,3])))
        with patch.dict(sys.modules, {'llm_adapters': llm, 'embedding_adapters': emb}):
            self.assertIn('成功', self.call('test_llm')['text'])
            self.assertEqual(self.call('test_embedding')['dimensions'], 3)

    def test_numeric_validation_does_not_reject_unrelated_false(self):
        (self.root/'Novel_directory.txt').write_text('blueprint')
        self.call('blueprint', should_enrich=False, unused_number=-1)
        with self.assertRaises(ValueError):
            self.call('draft', chapter_num=True)


class ManagerTests(unittest.TestCase):
    def test_snapshots_single_worker_sanitized_results_and_history(self):
        manager = web_tasks.TaskManager(max_history=2)
        entered, release = threading.Event(), threading.Event()
        calls = []
        def fake(action, path, config, params):
            calls.append((config, params))
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(3))
            return {'text': config['api_key'], 'items': ['safe']}
        try:
            with patch('web_tasks.dispatch', side_effect=fake):
                config, params = {'api_key': 'top-secret'}, {'nested': {'value': 1}}
                task = manager.submit('draft', '.', config, params)
                self.assertTrue(entered.wait(3))
                config['api_key'] = 'changed'
                params['nested']['value'] = 9
                task['logs'].append('mutated')
                second = manager.submit('draft', '.', {}, {})
                self.assertTrue(manager.has_active())
                with self.assertRaises(ValueError):
                    manager.submit('draft', '.', {}, {})
                release.set()
                manager.shutdown(wait=True)
            saved = manager.get(task['id'])
            self.assertEqual(calls[0][0]['api_key'], 'top-secret')
            self.assertEqual(calls[0][1]['nested']['value'], 1)
            self.assertEqual(saved['result']['text'], '[REDACTED]')
            saved['result']['items'].append('mutated')
            self.assertEqual(manager.get(task['id'])['result']['items'], ['safe'])
            self.assertEqual(manager.get(second['id'])['status'], 'failed')
            self.assertNotIn('api_key', manager.get(second['id'])['error'])
            self.assertFalse(manager.has_active())
        finally:
            release.set()
            manager.shutdown(wait=True)

    def test_error_never_exposes_sdk_credentials(self):
        manager = web_tasks.TaskManager()
        with patch('web_tasks.dispatch', side_effect=RuntimeError('Authorization Bearer unknown-token')):
            task = manager.submit('test_llm', '.', {}, {})
            manager.shutdown(wait=True)
        self.assertEqual(manager.get(task['id'])['status'], 'failed')
        self.assertNotIn('unknown-token', manager.get(task['id'])['error'])

    def test_finished_history_is_evicted_and_snapshot_is_independent(self):
        manager = web_tasks.TaskManager(max_history=1)
        finished = threading.Event()
        def fake(*args):
            finished.set()
            return {'text': 'ok'}
        with patch('web_tasks.dispatch', side_effect=fake):
            first = manager.submit('plot_arcs', '.', {}, {})
            self.assertTrue(finished.wait(3))
            # Wait on the worker barrier instead of polling task state.
            manager._executor.submit(lambda: None).result(timeout=3)
            second = manager.submit('plot_arcs', '.', {}, {})
            manager.shutdown(wait=True)
        self.assertIsNone(manager.get(first['id']))
        self.assertEqual(manager.get(second['id'])['status'], 'completed')
        with self.assertRaises(RuntimeError):
            manager.submit('plot_arcs', '.', {}, {})

    def test_reject_multiple_workers(self):
        with self.assertRaises(ValueError):
            web_tasks.TaskManager(2)


if __name__ == '__main__':
    unittest.main()
