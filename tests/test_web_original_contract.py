"""Bind Web adapter calls against original Python signatures, without API access."""
import ast
import inspect
from pathlib import Path
import tempfile
import types
import sys
import unittest
from unittest.mock import patch
from config_manager import get_default_config

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = {
    'Novel_architecture_generate': 'novel_generator/architecture.py',
    'Chapter_blueprint_generate': 'novel_generator/blueprint.py',
    'build_chapter_prompt': 'novel_generator/chapter.py',
    'generate_chapter_draft': 'novel_generator/chapter.py',
    'finalize_chapter': 'novel_generator/finalization.py',
    'enrich_chapter_text': 'novel_generator/finalization.py',
    'import_knowledge_file': 'novel_generator/knowledge.py',
    'clear_vector_store': 'novel_generator/vectorstore_utils.py',
    'check_consistency': 'consistency_checker.py',
}

def signature(name):
    tree = ast.parse((ROOT / FUNCTIONS[name]).read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    fn.body = [ast.Return(ast.Constant(None))]
    ns = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), '<original-signature>', 'exec'), ns)
    return inspect.signature(ns[name])


class OriginalContractTests(unittest.TestCase):
    def test_adapter_imports_original_apis_not_desktop_widgets(self):
        tree = ast.parse((ROOT / 'web_tasks.py').read_text(encoding='utf-8'))
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertIn('novel_generator', modules)
        self.assertIn('consistency_checker', modules)
        self.assertFalse(any(name and (name == 'tkinter' or name.startswith('ui.')) for name in modules))

    def test_web_actions_bind_original_signatures_and_stage_models(self):
        import web_tasks
        calls = []
        fake = types.ModuleType('novel_generator')
        checker = types.ModuleType('consistency_checker')
        for name in FUNCTIONS:
            sig = signature(name)
            def checked(*args, _name=name, _sig=sig, **kwargs):
                bound = _sig.bind(*args, **kwargs)
                calls.append((_name, dict(bound.arguments)))
                return '测试正文与提示词' if _name in {'build_chapter_prompt','generate_chapter_draft','enrich_chapter_text','check_consistency'} else None
            setattr(checker if name == 'check_consistency' else fake, name, checked)
        cfg = get_default_config()
        for name, model in cfg['llm_configs'].items():
            model['api_key'] = 'unit-test-secret'
        params = dict(cfg['other_params'], topic='测试', num_chapters=3, word_number=100,
                      chapter_num=1, user_guidance='遵循设定', chapter_text='测试正文',
                      should_enrich=False, custom_prompt_text='定制提示词')
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'novel_generator': fake, 'consistency_checker': checker}):
            project = Path(directory)
            (project / 'chapters').mkdir()
            (project / 'chapters/chapter_1.txt').write_text('原正文',encoding='utf-8')
            (project / 'Novel_architecture.txt').write_text('原设定',encoding='utf-8')
            (project / 'Novel_directory.txt').write_text('第1章：原蓝图',encoding='utf-8')
            for action in ['architecture','blueprint','build_prompt','draft','finalize','consistency']:
                with self.subTest(action=action):
                    web_tasks.dispatch(action, directory, cfg, dict(params))
        mapped = dict(calls)
        for action, function, field in [('architecture','Novel_architecture_generate','llm_model'),
                                        ('blueprint','Chapter_blueprint_generate','llm_model'),
                                        ('draft','generate_chapter_draft','model_name'),
                                        ('finalize','finalize_chapter','model_name'),
                                        ('consistency','check_consistency','model_name')]:
            stages = {'architecture':'architecture_llm','blueprint':'chapter_outline_llm',
                      'draft':'prompt_draft_llm','finalize':'final_chapter_llm','consistency':'consistency_review_llm'}
            expected = cfg['llm_configs'][cfg['choose_configs'][stages[action]]]['model_name']
            self.assertEqual(mapped[function][field],expected)


if __name__ == '__main__':
    unittest.main()
