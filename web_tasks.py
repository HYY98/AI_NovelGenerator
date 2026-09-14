"""Headless, serialized adapters around the original desktop business functions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
import importlib
import hashlib
import io
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid

_GLOBAL_LOCK = threading.RLock()
ACTIONS = frozenset(('architecture', 'blueprint', 'build_prompt', 'draft', 'finalize',
                     'consistency', 'batch', 'knowledge_import', 'clear_vectorstore',
                     'plot_arcs', 'test_llm', 'test_embedding'))
STAGES = {'architecture': 'architecture_llm', 'blueprint': 'chapter_outline_llm',
          'build_prompt': 'prompt_draft_llm', 'draft': 'prompt_draft_llm',
          'finalize': 'final_chapter_llm', 'consistency': 'consistency_review_llm'}


def _sanitize(text, config):
    values = []
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if any(word in str(key).lower() for word in ('api_key', 'password', 'secret', 'access_token', 'auth_token')) and isinstance(item, str) and item:
                    values.append(item)
                else:
                    walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
    walk(config)
    output = str(text)
    for value in sorted(set(values), key=len, reverse=True):
        output = output.replace(value, '[REDACTED]')
    output = re.sub(r'(?:sk-|gh[pousr]_)[A-Za-z0-9_-]{8,}|github_pat_[A-Za-z0-9_]+', '[REDACTED]', output)
    output = re.sub(r'(https?://)[^\s/@:]+:[^\s/@]+@', r'\1[REDACTED]@', output)
    return output


def _clean_result(value, config):
    if isinstance(value, str):
        return _sanitize(value, config)
    if isinstance(value, dict):
        return {key: _clean_result(item, config) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_result(item, config) for item in value]
    return value


def _validate(project_path, params):
    if not isinstance(project_path, (str, os.PathLike)) or not str(project_path).strip():
        raise ValueError('project_path required')
    if not isinstance(params, dict):
        raise ValueError('params must be an object')


def _positive(value, name, default):
    if value is None or value == '':
        value = default
    if isinstance(value, bool):
        raise ValueError(name + ' must be a positive integer')
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise ValueError(name + ' must be a positive integer') from None
    if result <= 0 or result > 100000 or (isinstance(value, float) and value != result):
        raise ValueError(name + ' is out of range')
    return result


def _boolean(value, name):
    if not isinstance(value, bool):
        raise ValueError(name + ' must be boolean')
    return value


def _text(value, name):
    if not isinstance(value, str):
        raise ValueError(name + ' must be text')
    return value


def _llm(cfg, action, params):
    configs = cfg.get('llm_configs')
    if isinstance(configs, dict):
        selected = cfg.get('choose_configs', {}).get(STAGES.get(action))
        if action == 'test_llm':
            selected = params.get('llm_config_name', params.get('config_name', cfg.get('last_llm_config_name')))
        if not selected:
            selected = cfg.get('last_llm_config_name') or next(iter(configs), None)
        if selected not in configs:
            raise ValueError('Selected model configuration does not exist')
        model = configs[selected]
    else:
        model = cfg.get('llm', cfg.get('llm_config', cfg))
    return {'api_key': model.get('api_key', ''), 'base_url': model.get('base_url', ''),
            'model_name': model.get('model_name', model.get('llm_model', '')),
            'interface_format': model.get('interface_format', 'OpenAI'),
            'temperature': model.get('temperature', 0.7),
            'max_tokens': _positive(model.get('max_tokens'), 'max_tokens', 8192),
            'timeout': _positive(model.get('timeout'), 'timeout', 600)}


def _embedding(cfg, params):
    configs = cfg.get('embedding_configs', {})
    selected = params.get('embedding_config_name', cfg.get('last_embedding_interface_format'))
    if configs:
        selected = selected or next(iter(configs))
        if selected not in configs:
            raise ValueError('Selected embedding configuration does not exist')
        model = configs[selected]
    else:
        model = cfg.get('embedding', cfg.get('embedding_config', {}))
    return {'embedding_api_key': model.get('api_key', ''),
            'embedding_url': model.get('base_url', 'https://api.openai.com/v1'),
            'embedding_interface_format': model.get('interface_format', selected or 'OpenAI'),
            'embedding_model_name': model.get('model_name', 'text-embedding-3-small')}, _positive(model.get('retrieval_k'), 'retrieval_k', 4)


class _DiscardOutput(io.TextIOBase):
    def write(self, text):
        return len(text)
    def flush(self):
        pass


@contextmanager
def _environment(cfg, params):
    # Original prompt modules and environment are process globals: serialize all adapters.
    import config_manager
    import prompt_definitions
    original_flag = config_manager.IS_ENGLISH
    original_prompts = {k: v for k, v in vars(prompt_definitions).items() if not k.startswith('__')}
    env_keys = ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')
    old_env = {key: os.environ.get(key) for key in env_keys}
    old_logging = logging.root.manager.disable
    english = params.get('english_mode', params.get('is_english', cfg.get('english_mode', cfg.get('is_english', False))))
    _boolean(english, 'english_mode')
    try:
        importlib.reload(prompt_definitions)
        config_manager.IS_ENGLISH = english
        if english:
            source = importlib.import_module('prompt_definitions_en')
            for key, value in vars(source).items():
                if not key.startswith('__'):
                    setattr(prompt_definitions, key, value)
        proxy = cfg.get('proxy_setting', {})
        if proxy.get('enabled', False):
            host = str(proxy.get('proxy_url', '127.0.0.1')).strip()
            if '://' not in host:
                host = 'http://' + host
            port = str(proxy.get('proxy_port', '')).strip()
            address = host.rstrip('/') + (':' + port if port else '')
            for key in env_keys:
                os.environ[key] = address
        else:
            for key in env_keys:
                os.environ.pop(key, None)
        # Original utilities print prompts and responses; do not leak credentials to task logs.
        logging.disable(logging.CRITICAL)
        with redirect_stdout(_DiscardOutput()), redirect_stderr(_DiscardOutput()):
            yield
    finally:
        config_manager.IS_ENGLISH = original_flag
        for key in list(vars(prompt_definitions)):
            if not key.startswith('__'):
                delattr(prompt_definitions, key)
        for key, value in original_prompts.items():
            setattr(prompt_definitions, key, value)
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        logging.disable(old_logging)


def _read(root, filename):
    path = root / filename
    return path.read_text(encoding='utf-8-sig') if path.is_file() else ''


def _atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=path.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _roles(root, params):
    names = params.get('selected_roles', params.get('characters_involved', ''))
    if isinstance(names, str):
        names = re.split(r'[,，\n]', names)
    if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
        raise ValueError('selected_roles must be a list of names')
    selected = {name.strip() for name in names if name.strip()}
    library = (root / '角色库').resolve()
    texts = []
    if selected and library.is_dir():
        for path in sorted(library.rglob('*.txt')):
            if library in path.resolve().parents and path.stem in selected:
                texts.append(path.read_text(encoding='utf-8-sig').strip())
    return '\n\n'.join(texts)


def _dispatch(action, root, cfg, p):
    if action == 'plot_arcs':
        return {'text': _read(root, 'plot_arcs.txt')}
    if action == 'clear_vectorstore':
        if p.get('confirm') is not True:
            raise ValueError('clear_vectorstore requires confirm=true')
        from novel_generator import clear_vector_store
        success = clear_vector_store(str(root))
        if success is False:
            raise RuntimeError('Vector store cleanup failed')
        return {'text': '向量库已清空', 'success': True}
    embedding, retrieval = _embedding(cfg, p)
    if action == 'knowledge_import':
        from novel_generator import import_knowledge_file
        content = p.get('text', p.get('knowledge_text'))
        if content is not None:
            content = _text(content, 'text')
            if not content.strip():
                raise ValueError('Knowledge text is empty')
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'knowledge.txt'
                path.write_text(content, encoding='utf-8')
                import_knowledge_file(file_path=str(path), filepath=str(root), **embedding)
        else:
            path = Path(_text(p.get('file_path', p.get('knowledge_file', '')), 'file_path')).expanduser()
            if not str(p.get('file_path', p.get('knowledge_file', ''))).strip() or not path.is_file():
                raise ValueError('Knowledge file does not exist')
            import_knowledge_file(file_path=str(path.resolve()), filepath=str(root), **embedding)
        return {'text': '知识文件导入完成'}
    if action == 'test_embedding':
        from embedding_adapters import create_embedding_adapter
        adapter = create_embedding_adapter(api_key=embedding['embedding_api_key'], base_url=embedding['embedding_url'], interface_format=embedding['embedding_interface_format'], model_name=embedding['embedding_model_name'])
        vector = adapter.embed_query('连接测试')
        if vector is None or len(vector) == 0:
            raise RuntimeError('Embedding service returned no vector')
        return {'text': 'Embedding 连接测试成功', 'dimensions': len(vector)}
    if action == 'batch':
        start = _positive(p.get('start_chapter', p.get('start')), 'start_chapter', 1)
        end = _positive(p.get('end_chapter', p.get('end')), 'end_chapter', start)
        if end < start or end - start >= 1000:
            raise ValueError('Invalid batch chapter range')
        completed = []
        for number in range(start, end + 1):
            item = dict(p, chapter_num=number, novel_number=number)
            item.pop('custom_prompt_text', None)
            item.pop('chapter_revision', None)
            draft = _dispatch('draft', root, cfg, item)
            item['chapter_text'] = draft['text']
            minimum = _positive(p.get('min_word_number', p.get('min_word')), 'min_word_number', _positive(p.get('word_number'), 'word_number', 3000))
            from utils import get_word_count
            item['should_enrich'] = _boolean(p.get('auto_enrich', False), 'auto_enrich') and get_word_count(draft['text']) < 0.7 * minimum
            final = _dispatch('finalize', root, cfg, item)
            completed.append(number)
        return {'text': final['text'], 'chapters': completed, 'chapter_num': end}
    llm = _llm(cfg, action, p)
    if action == 'test_llm':
        from llm_adapters import create_llm_adapter
        response = create_llm_adapter(**llm).invoke('请回复 OK。')
        if not response:
            raise RuntimeError('Model returned no response')
        return {'text': 'LLM 连接测试成功'}
    from novel_generator import Novel_architecture_generate, Chapter_blueprint_generate, build_chapter_prompt, generate_chapter_draft, finalize_chapter, enrich_chapter_text
    guidance = _text(p.get('user_guidance', ''), 'user_guidance')
    if action in ('architecture', 'blueprint'):
        model = dict(llm)
        model['llm_model'] = model.pop('model_name')
        args = dict(filepath=str(root), number_of_chapters=_positive(p.get('number_of_chapters', p.get('num_chapters')), 'num_chapters', 10), user_guidance=guidance, **model)
        if action == 'architecture':
            Novel_architecture_generate(topic=_text(p.get('topic', ''), 'topic'), genre=_text(p.get('genre', ''), 'genre'), word_number=_positive(p.get('word_number'), 'word_number', 3000), **args)
            text = _read(root, 'Novel_architecture.txt')
        else:
            Chapter_blueprint_generate(**args)
            text = _read(root, 'Novel_directory.txt')
        if not text.strip():
            raise RuntimeError('Generation did not produce a document')
        return {'text': text}
    number = _positive(p.get('novel_number', p.get('chapter_num')), 'chapter_num', 1)
    word_number = _positive(p.get('word_number'), 'word_number', 3000)
    chapter_path = root / 'chapters' / f'chapter_{number}.txt'
    if action in ('build_prompt', 'draft'):
        args = dict(llm, **embedding, filepath=str(root), novel_number=number, word_number=word_number,
                    user_guidance=guidance, embedding_retrieval_k=retrieval)
        for field in ('characters_involved', 'key_items', 'scene_location', 'time_constraint'):
            args[field] = _text(p.get(field, ''), field)
        prompt = p.get('custom_prompt_text') if action == 'draft' else None
        if prompt is None:
            prompt = build_chapter_prompt(**args)
            roles = _roles(root, p)
            if roles:
                import config_manager
                prompt += '\n\n' + ('Core characters:' if config_manager.IS_ENGLISH else '核心人物：') + '\n' + roles
        prompt = _text(prompt, 'custom_prompt_text')
        if action == 'build_prompt':
            return {'text': prompt, 'chapter_num': number}
        text = generate_chapter_draft(**args, custom_prompt_text=prompt)
        if not text or not text.strip():
            raise RuntimeError('Model returned an empty draft')
        return {'text': text, 'chapter_num': number}
    if action == 'finalize':
        expected_revision = p.get('chapter_revision')
        def check_revision():
            if expected_revision is not None:
                data = chapter_path.read_bytes() if chapter_path.is_file() else b''
                if expected_revision != hashlib.sha256(data).hexdigest():
                    raise ValueError('Chapter changed on disk; reload before finalizing')
        check_revision()
        text = _text(p.get('chapter_text', _read(root, f'chapters/chapter_{number}.txt')), 'chapter_text')
        if not text.strip():
            raise ValueError('Chapter text is empty')
        if _boolean(p.get('should_enrich', False), 'should_enrich'):
            text = enrich_chapter_text(chapter_text=text, word_number=word_number, **llm)
            if not text or not text.strip():
                raise RuntimeError('Model returned an empty enriched chapter')
        check_revision()
        _atomic_text(chapter_path, text)
        finalize_chapter(novel_number=number, word_number=word_number, filepath=str(root), **llm, **embedding)
        return {'text': _read(root, f'chapters/chapter_{number}.txt'), 'chapter_num': number}
    if action == 'consistency':
        from consistency_checker import check_consistency
        from card_store import build_card_context
        text = _text(p.get('chapter_text', _read(root, f'chapters/chapter_{number}.txt')), 'chapter_text')
        if not text.strip():
            raise ValueError('Chapter text is empty')
        result = check_consistency(novel_setting=_read(root, 'Novel_architecture.txt') + '\n\n' + build_card_context(root), character_state=_read(root, 'character_state.txt'), global_summary=_read(root, 'global_summary.txt'), chapter_text=text, plot_arcs=_read(root, 'plot_arcs.txt'), **llm)
        return {'text': result or '', 'chapter_num': number}
    raise ValueError('Unsupported action')


def dispatch(action, project_path, config_snapshot, params):
    _validate(project_path, params)
    if action not in ACTIONS:
        raise ValueError('Unsupported action')
    cfg = deepcopy(config_snapshot or {})
    if not isinstance(cfg, dict):
        raise ValueError('Configuration must be an object')
    p = deepcopy(cfg.get('other_params', {}))
    p.update(deepcopy(params))
    root = Path(project_path).expanduser().resolve()
    with _GLOBAL_LOCK, _environment(cfg, p):
        result = _dispatch(action, root, cfg, p)
    return _clean_result(result, cfg)


class TaskManager:
    def __init__(self, max_workers=1, max_history=100):
        if max_workers != 1:
            raise ValueError('Original pipeline requires a single worker')
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='novel-task')
        self._tasks = {}
        self._lock = threading.RLock()
        self._max_history = max(1, int(max_history))
        self._closed = False

    def submit(self, action, project_path, config_snapshot, params):
        _validate(project_path, params)
        if action not in ACTIONS:
            raise ValueError('Unsupported action')
        cfg, request = deepcopy(config_snapshot or {}), deepcopy(params)
        with self._lock:
            if self._closed:
                raise RuntimeError('Task manager is closed')
            while len(self._tasks) >= self._max_history:
                old = next((key for key, task in self._tasks.items() if task['status'] in ('completed', 'failed')), None)
                if old is None:
                    raise ValueError('Task queue is full')
                del self._tasks[old]
            tid = uuid.uuid4().hex
            task = {'id': tid, 'action': action, 'status': 'queued', 'logs': ['任务已排队'], 'result': None, 'error': None}
            self._tasks[tid] = task
            self._executor.submit(self._run, tid, action, str(project_path), cfg, request)
            return deepcopy(task)

    def _run(self, tid, action, path, cfg, params):
        with self._lock:
            self._tasks[tid].update(status='running', logs=['任务已开始'])
        try:
            result = dispatch(action, path, cfg, params)
            with self._lock:
                self._tasks[tid].update(status='completed', result=_clean_result(result, cfg), logs=['任务已完成'])
        except Exception:
            # SDK exceptions may echo request headers, endpoint credentials or entire prompts.
            with self._lock:
                self._tasks[tid].update(status='failed', error='任务执行失败，请检查参数、模型配置、网络连接和项目文件。', logs=['任务执行失败'])

    def get(self, task_id):
        with self._lock:
            return deepcopy(self._tasks.get(task_id))

    def has_active(self):
        with self._lock:
            return any(task['status'] in ('queued', 'running') for task in self._tasks.values())

    def shutdown(self, wait=False):
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait)
