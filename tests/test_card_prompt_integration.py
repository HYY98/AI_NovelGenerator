"""Verify card context reaches first and subsequent chapter prompts offline."""
import ast
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock


class CardPromptIntegrationTest(unittest.TestCase):
    def build(self, number, context):
        source = Path('novel_generator/chapter.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_chapter_prompt')
        module = ast.Module(body=[function], type_ignores=[])
        info = dict.fromkeys(['chapter_title', 'chapter_role', 'chapter_purpose', 'suspense_level',
                             'foreshadowing', 'plot_twist_level', 'chapter_summary'], '')
        prompts = types.SimpleNamespace(first_chapter_draft_prompt='{novel_setting}',
                                        next_chapter_draft_prompt='{user_guidance}',
                                        knowledge_search_prompt='{user_guidance}')
        import os
        import logging
        env = dict(os=os, logging=logging, read_file=lambda p: '',
                   build_card_context=lambda p: context,
                   get_chapter_info_from_blueprint=lambda *a: info,
                   prompt_definitions=prompts, get_last_n_chapters_text=lambda *a, **k: [],
                   summarize_recent_chapters=Mock(return_value=''),
                   create_llm_adapter=Mock(side_effect=RuntimeError('offline')))
        exec(compile(module, '<chapter-prompt-test>', 'exec'), env)
        with tempfile.TemporaryDirectory() as root:
            return env['build_chapter_prompt'](
                api_key='', base_url='', model_name='', filepath=root,
                novel_number=number, word_number=100, temperature=0.7,
                user_guidance='guidance', characters_involved='', key_items='',
                scene_location='', time_constraint='', embedding_api_key='',
                embedding_url='', embedding_interface_format='', embedding_model_name='')

    def test_first_chapter_contains_cards(self):
        self.assertIn('CARD_CONTEXT', self.build(1, 'CARD_CONTEXT'))

    def test_later_chapter_contains_cards(self):
        self.assertIn('CARD_CONTEXT', self.build(2, 'CARD_CONTEXT'))

    def test_empty_library_keeps_prompt(self):
        self.assertEqual('', self.build(1, ''))

    def test_consistency_review_includes_architecture_and_cards(self):
        tree = ast.parse(Path('ui/generation_handlers.py').read_text(encoding='utf-8'))
        check = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name) and n.func.id == 'check_consistency')
        setting = next(k.value for k in check.keywords if k.arg == 'novel_setting')
        import os
        value = eval(compile(ast.Expression(setting), '<consistency-test>', 'eval'),
                     dict(os=os, filepath='project', read_file=lambda p: 'ARCHITECTURE',
                          build_card_context=lambda p: 'CARDS'))
        self.assertIn('ARCHITECTURE', value)
        self.assertIn('CARDS', value)


if __name__ == '__main__':
    unittest.main()
