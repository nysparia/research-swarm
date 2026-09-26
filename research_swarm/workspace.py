"""Conversation-owned research tasks, versioned Markdown and isolated execution."""
from __future__ import annotations

import copy
import io
import json
import re
import threading
import uuid
import zipfile
from pathlib import Path

from .providers import Settings, parse_json_object
from .server import APP_ROOT, ResearchApplication, export_bundle, utc_now
from .paper import new_paper, sync_paper, paper_view, edit_section, validate_topics, export_paper, source_signature, BUDGETS
from .paper_prompts import TOPIC_PROMPT


def identity():
    return uuid.uuid4().hex


class WorkspaceApplication:
    def __init__(self, source: Path, state_dir: Path, max_workers=8, static_dir=None, import_existing=True):
        self.source = Path(source).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.static_dir = Path(static_dir or APP_ROOT / 'dist')
        self.max_workers = max_workers
        # One provider for every research task. Do not inherit unrelated source credentials.
        self.settings = Settings(self.state_dir, None)
        self._lock = threading.RLock()
        self._mutation_lock = threading.RLock()
        self._apps_lock = threading.RLock()
        self._records, self._apps, self._threads = {}, {}, []
        self._closed = False
        self._stop_event = threading.Event()
        self._configuring = False
        self._data_root = self.state_dir / 'tasks'
        self._data_root.mkdir(exist_ok=True)
        for path in self._data_root.glob('*/conversation.json'):
            if not re.fullmatch(r'[a-f0-9]{32}', path.parent.name):
                continue
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
                if record['id'] != path.parent.name:
                    continue
                if record['phase'] in ('retrieving', 'researching') or record['document']['polishing']:
                    if record['phase'] != 'researching':
                        record['phase'] = 'requirements'
                    record['document']['polishing'] = False
                    record['error'] = '上次服务停止，已保留需求和执行记录。可以继续研究。'
                    record['token'] += 1
                self._records[record['id']] = record
                record.setdefault('paper', new_paper())
            except (ValueError, KeyError, TypeError):
                continue
        marker = self.state_dir / 'source-imported.json'
        if import_existing and not marker.exists():
            try:
                from .library import Library
                library = Library(self.source).load()
                if library.get('papers'):
                    topic = library.get('topic', {})
                    title = topic.get('title') or '已有科研课题'
                    record = self._create(title, imported=True)
                    draft = self._local_draft(topic.get('description') or title, '', False)
                    record['document'].update(markdown=draft['markdown'], revision=1, source='local')
                    record['compiled'] = draft
                    record['needsRetrieval'] = False
                    record['phase'] = 'requirements'
                    self._message(record, 'assistant', f'已接入原课题的 {len(library.get("papers", []))} 篇论文。可编辑需求或直接开始研究，资料会复制到本任务独立目录。', 'requirements')
                    self._save(record)
                marker.write_text(json.dumps({'source': str(self.source), 'at': utc_now()}), encoding='utf-8')
            except (ValueError, OSError):
                # Missing source remains an actionable task error at research start.
                pass
        self._supervisor = threading.Thread(target=self._supervise, daemon=True, name='research-records')
        self._supervisor.start()

    def _supervise(self):
        while not self._stop_event.wait(.5):
            with self._lock:
                if self._closed:
                    return
                for record in self._records.values():
                    try:
                        self._synchronize(record)
                    except (OSError, ValueError) as exc:
                        record['error'] = '研究记录保存未完成：' + self.settings.safe_error(exc)

    def _create(self, title='新科研任务', imported=False):
        task_id = identity()
        record = {'id': task_id, 'title': title[:80], 'phase': 'empty', 'createdAt': utc_now(), 'updatedAt': utc_now(),
                  'round': 1, 'imported': imported, 'needsRetrieval': True, 'token': 0, 'error': None, 'messages': [], 'runs': [],
                  'document': {'markdown': '', 'revision': 0, 'polishing': False, 'polishedFrom': None, 'source': 'local', 'questions': [], 'error': None},
                  'documentHistory': [], 'compiled': None, 'paper': new_paper()}
        self._records[task_id] = record
        self._save(record)
        return record

    def _record(self, task_id):
        if not re.fullmatch(r'[a-f0-9]{32}', task_id) or task_id not in self._records:
            raise ValueError('科研任务不存在')
        return self._records[task_id]

    def _save(self, record):
        record['updatedAt'] = utc_now()
        directory = self._data_root / record['id']
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'conversation.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)

    @staticmethod
    def _message(record, role, content, kind=None):
        record['messages'].append({'id': identity(), 'role': role, 'content': content, 'at': utc_now(), 'kind': kind})

    @staticmethod
    def _summary(record):
        return {key: record[key] for key in ('id', 'title', 'phase', 'updatedAt', 'round')}

    def tasks(self):
        with self._lock:
            for record in self._records.values():
                self._synchronize(record)
            return [self._summary(r) for r in sorted(self._records.values(), key=lambda r: r['updatedAt'], reverse=True)]

    def _synchronize(self, record):
        app = self._apps.get(record['id'])
        if not app or record['phase'] != 'researching':
            return
        state = app.snapshot()
        if sync_paper(record['paper'], state):
            self._save(record)
        if state['status'] == 'completed':
            record['phase'] = 'completed'
            record['round'] = state['project']['round']
            self._message(record, 'assistant', state['report']['summary'], 'result')
            run = {'round': record['round'], 'at': utc_now(), 'summary': state['report']['summary'], 'mode': state['project']['mode']}
            record['runs'] = [r for r in record['runs'] if r['round'] != run['round']] + [run]
            state['executionHistory'] = app.engine.export_audit()
            directory = self._data_root / record['id'] / 'reports'
            directory.mkdir(exist_ok=True)
            (directory / f'round-{run["round"]}.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
            self._save(record)
        elif state['status'] == 'failed':
            record['phase'] = 'failed'
            failed = [n for n in state['nodes'] if n['active'] and n['status'] == 'failed']
            reasons = [n['title']+'：'+(n.get('error') or {}).get('message','执行失败，请查看节点记录') for n in failed]
            record['error'] = '\n'.join(reasons)
            self._message(record, 'assistant', f'{len(failed)} 个节点执行失败：\n'+'\n'.join('- '+reason for reason in reasons)+'\n已完成结果保留，失败原因和原始错误可在节点历史中查看。', 'progress')
            self._save(record)

    def detail(self, task_id):
        with self._lock:
            record = self._record(task_id)
            self._synchronize(record)
            app = self._apps.get(task_id)
            state = app.snapshot() if app else None
            # Completed tasks remain inspectable without recreating any model worker on restart.
            if not state and record['runs']:
                path = self._data_root / task_id / 'reports' / f'round-{record["runs"][-1]["round"]}.json'
                if path.is_file():
                    state = json.loads(path.read_text(encoding='utf-8'))
            if sync_paper(record['paper'], state):
                self._save(record)
            artifacts = []
            if record['phase'] == 'completed':
                artifacts = [{'name': '研究报告与数据.zip', 'kind': 'archive', 'url': f'/api/tasks/{task_id}/export'},
                             {'name': '需求文档.md', 'kind': 'requirements', 'url': f'/api/tasks/{task_id}/document'}]
            if state:
                seen = set()
                root = (self._data_root / task_id / 'runtime').resolve()
                executions = [{**entry['execution'], 'valid': entry.get('valid') is True}
                              for entry in reversed(state.get('history', [])) if entry.get('type') in ('tool-executed', 'tool-started')]
                executions.extend(execution for node in state.get('nodes', [])
                                  for execution in ((node.get('output') or {}).get('structured') or {}).get('executions', []))
                for execution in executions:
                    items = list(execution.get('artifacts') or [])
                    items.extend({'path': execution[key]} for key in ('script', 'stdoutPath', 'stderrPath', 'receipt') if execution.get(key))
                    for item in items:
                        relative = str(item.get('path', ''))
                        path = (root / relative).resolve()
                        if relative not in seen and path.is_relative_to(root / 'runs') and path.is_file():
                            seen.add(relative)
                            artifacts.append({'name': item.get('name') or path.name, 'kind': 'experiment',
                                              'url': f'/api/tasks/{task_id}/artifacts/{relative}', 'path': relative,
                                              'nodeId': execution.get('nodeId'), 'nodeVersion': execution.get('nodeVersion'),
                                              'round': execution.get('round'), 'status': execution.get('status'),
                                              'valid': bool(execution.get('valid')), 'createdAt': execution.get('createdAt')})
            return copy.deepcopy({'task': self._summary(record), 'phase': record['phase'], 'document': record['document'],
                                  'messages': record['messages'], 'state': state, 'error': record['error'], 'artifacts': artifacts,
                                  'runs': record['runs'], 'paper': paper_view(record['paper'], state),
                                  'modelReady': self.settings.public()['capabilities']['modelReady']})

    def _spawn(self, function, *args):
        thread = threading.Thread(target=function, args=args, daemon=True, name='research-conversation')
        self._threads.append(thread)
        thread.start()

    @staticmethod
    def _local_draft(text, previous, editing):
        if editing:
            markdown = text.strip()
        elif previous:
            markdown = previous.rstrip() + '\n\n## 本轮补充\n\n' + text.strip()
        else:
            markdown = '# 科研需求\n\n## 研究问题\n\n' + text.strip() + '\n\n## 希望得到的结果\n\n- 总结现有方案、主要差异和适用条件\n- 给出可追溯的证据与局限，区分已知事实和待验证设想\n- 提供下一步研究建议与可执行实验设计\n\n## 约束与验收\n\n- 不编造论文、数据或已完成实验\n- 结果直接面向用户，论文阅读由研究节点承担\n- 没有可比证据时明确说明，不预设优胜方案\n\n## 待明确\n\n- 研究对象、评测数据与可用计算预算有哪些限制？\n'
        headline = re.sub(r'[#*`\n]+', ' ', text).strip()[:42] or '新科研任务'
        return {'title': headline, 'markdown': markdown, 'summary': '已保存需求草稿。配置 DeepSeek API Key 后，模型会润色需求并主动补充澄清建议；当前为本地文档整理。',
                'questions': ['是否有必须比较的方案、指定数据集或时间/计算预算？'], 'source': 'local',
                'requirements': [{'id': 'requirement:1', 'description': markdown, 'acceptance': '回答用户研究问题，所有事实有证据位置；明确假设、局限和未决问题。', 'constraints': '不编造论文或实验结果；不要求用户自行阅读论文。'}],
                'queries': [re.sub(r'[#*`\n]+', ' ', text).strip()[:300]]}

    def _draft(self, text, previous, editing=False, research_context=None):
        if not self.settings.public()['capabilities']['modelReady']:
            return self._local_draft(text, previous, editing)
        prompt = '''你是计算机科研需求协作者。把用户自然语言或编辑后的Markdown整理成清晰、可研究的需求文档。保留用户目的、修改、约束和未确定事项，不能擅自定范围或实验结论；缺失条件以最多3个可选澄清问题引导，不阻止合理开始。用户不需要读论文，研究由节点完成。
只返回JSON: {"title":"简洁课题名","markdown":"完整Markdown需求文档","summary":"一段简短修改说明或回应","questions":["问题"],"requirements":[{"id":"requirement:1","description":"具体研究需求","acceptance":"验收标准","constraints":"约束"}],"queries":["英文精确学术检索式"]}。requirements须完整覆盖MD，最多8条；queries最多4条，分别覆盖具体方法、基线、部署或验证，使用2–6个公认英文术语/具体方法名，不拼接整段愿望或否定修饰（例如无文本决策应检索 compact neural classifier、tabular MLP、TinyML inference，不检索 without text generation）。本机工具可采集环境、安装独立科研依赖、执行Python实验；不要把硬件/环境信息要求用户手动采集。缺少应用场景时保留未知，并提供最小可行实验候选及其适用范围。不得返回凭据或API配置。Markdown不含HTML、脚本。'''
        prompt += TOPIC_PROMPT
        result = parse_json_object(self.settings.chat([{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps({'mode': '用户刚编辑完，请保留编辑并润色' if editing else '对话补充需求', 'previousMarkdown': previous, 'userInput': text, 'previousResearch': research_context}, ensure_ascii=False)}], max_tokens=8500, json_mode=True))
        if not isinstance(result.get('markdown'), str) or not result['markdown'].strip() or len(result['markdown']) > 60000:
            raise ValueError('模型没有返回有效需求文档，用户编辑已保存，可重试润色')
        requirements = result.get('requirements')
        if not isinstance(requirements, list) or not 1 <= len(requirements) <= 8:
            raise ValueError('模型需求拆分格式无效，原文已保留')
        normalized = []
        for index, requirement in enumerate(requirements):
            if not isinstance(requirement, dict) or not all(isinstance(requirement.get(k), str) and requirement[k].strip() for k in ('description', 'acceptance')):
                raise ValueError('模型需求缺少描述或验收标准')
            normalized.append({'id': f'requirement:{index + 1}', 'description': requirement['description'][:15000], 'acceptance': requirement['acceptance'][:5000], 'constraints': str(requirement.get('constraints', ''))[:5000]})
        queries = result.get('queries')
        if not isinstance(queries, list) or not queries or any(not isinstance(q, str) or not q.strip() for q in queries):
            raise ValueError('模型未给出有效学术检索式，可修改需求后重试')
        return {'title': str(result.get('title') or '科研任务')[:80], 'markdown': result['markdown'].strip(),
                'summary': str(result.get('summary') or '需求文档已更新，请继续补充或开始研究。')[:2000],
                'questions': [str(q)[:600] for q in result.get('questions', [])[:3]], 'source': 'model', 'requirements': normalized,
                'queries': [q.strip()[:1000] for q in queries[:4]], 'topics': validate_topics(result.get('topics', []))}

    def _polish(self, task_id, token, revision, text, previous, editing):
        try:
            state = self.detail(task_id).get('state')
            previous_report = (state or {}).get('report')
            result = self._draft(text, previous, editing, previous_report)
            with self._lock:
                record = self._record(task_id)
                if self._closed or record['token'] != token or record['document']['revision'] != revision:
                    return
                record['documentHistory'].append({'at': utc_now(), 'actor': 'AI' if result['source'] == 'model' else 'system', 'revision': revision + 1, 'markdown': result['markdown'], 'reason': result['summary']})
                record['document'].update(markdown=result['markdown'], revision=revision + 1, polishing=False, polishedFrom=revision, source=result['source'], questions=result['questions'], error=None)
                record['compiled'] = result
                if not record['paper']['selectedTopicId']:
                    record['paper']['topics'] = result.get('topics', [])
                    record['paper']['revision'] += 1
                record['title'] = result['title']
                record['phase'] = 'requirements'
                self._message(record, 'assistant', result['summary'], 'requirements')
                self._save(record)
        except Exception as exc:
            with self._lock:
                record = self._record(task_id)
                if self._closed or record['token'] != token:
                    return
                record['document'].update(polishing=False, error=self.settings.safe_error(exc))
                self._message(record, 'assistant', '需求润色未完成，已保留你的原文。' + self.settings.safe_error(exc), 'requirements')
                self._save(record)

    def _ensure_app(self, task_id):
        with self._apps_lock:
            if task_id in self._apps:
                return self._apps[task_id]
            from .sources import prepare_source
            with self._lock:
                record = copy.deepcopy(self._record(task_id))
            root = self._data_root / task_id
            source = prepare_source(self.source, root / 'source', task_id, record['title'], record['document']['markdown'], import_existing=record['imported'])
            app = ResearchApplication(source, root / 'runtime', self.max_workers, workflow='autonomous')
            app.settings = self.settings
            app.runner.settings = self.settings
            app.mutation_lock = self._mutation_lock
            with self._lock:
                if self._closed:
                    app.engine.close()
                    raise ValueError('研究服务已停止')
                self._apps[task_id] = app
            return app

    def _run(self, task_id, token):
        try:
            app = self._ensure_app(task_id)
            with self._lock:
                record = self._record(task_id)
                compiled = copy.deepcopy(record['compiled'])
                imported = not record.get('needsRetrieval', True)
                if self._closed or record['token'] != token:
                    return
                metadata = (record['title'], record['document']['markdown'], record['imported'])
            from .sources import prepare_source
            prepare_source(self.source, self._data_root / task_id / 'source', task_id, metadata[0], metadata[1], import_existing=metadata[2])
            if not imported:
                for query in compiled['queries']:
                    with self._lock:
                        record = self._record(task_id)
                        if self._closed or record['token'] != token:
                            return
                        self._message(record, 'assistant', '正在检索支撑当前问题的论文：' + query, 'progress')
                        self._save(record)
                    result = app._retrieve({'query': query, 'topK': 8})
                    with self._lock:
                        if self._closed or self._record(task_id)['token'] != token:
                            return
                        app.engine.command('refresh-library', {'library': result['library']})
            with self._mutation_lock:
                with self._lock:
                    record = self._record(task_id)
                    if self._closed or record['token'] != token:
                        return
                    state = app.engine.snapshot()
                    if state['project'].get('researchStarted') or state['report'].get('ready') or record['runs']:
                        app.engine.command('next-round', {})
                    mode = 'llm' if self.settings.public()['capabilities']['modelReady'] else 'evidence'
                    state = app.engine.command('start-autonomous', {'requirements': compiled['requirements'], 'title': record['title'], 'mode': mode, 'allowNewSearch': True, 'searchBudgetId': identity(), 'markdown': record['document']['markdown'],
                        'paperResearch': True, 'paperContext': self._paper_context(record), 'budgetTier': record['paper'].get('budgetTier', 'swarm')})
                    record['phase'] = 'researching'
                    record['needsRetrieval'] = False
                    record['round'] = state['project']['round']
                    self._message(record, 'assistant', f'已建立 {len(state["facetNodes"])} 个资料节点。接下来围绕论文的论点、基线与可验证实验安排专业节点，研究成果会持续写入论文。涉及研究方向的关键取舍会请你决定。' + (' 当前未配置模型，执行已有资料核验。' if mode == 'evidence' else ''), 'progress')
                    self._save(record)
        except Exception as exc:
            with self._lock:
                record = self._record(task_id)
                if self._closed or record['token'] != token:
                    return
                record['phase'] = 'failed'
                record['error'] = self.settings.safe_error(exc)
                self._message(record, 'assistant', '研究暂未开始或已中断：' + record['error'], 'progress')
                self._save(record)

    def _edit(self, task_id, payload, editing):
        key = 'markdown' if editing else 'text'
        text = payload.get(key)
        if not isinstance(text, str) or not text.strip() or len(text) > 60000:
            raise ValueError('请输入 1 至 60000 字符的研究需求')
        with self._lock:
            if self._configuring:
                raise ValueError('正在验证模型配置，请稍后再试')
            record = self._record(task_id)
            self._synchronize(record)
            if editing and payload.get('expectedRevision') != record['document']['revision']:
                raise ValueError('需求文档版本已变化，请保留本地修改并重新同步')
            app = self._apps.get(task_id)
            if app:
                app.post('/api/actions/pause', {})
                app.engine.command('paper-context', {'paperContext': self._paper_context(record), 'supersedeDecision': True})
            previous = record['document']['markdown']
            record['token'] += 1
            revision = record['document']['revision'] + 1
            record['phase'], record['error'], record['compiled'] = 'requirements', None, None
            record['needsRetrieval'] = True
            if not editing:
                self._message(record, 'user', text)
            raw = text if editing else previous + ('\n\n## 用户补充\n\n' if previous else '') + text
            record['documentHistory'].append({'at': utc_now(), 'actor': 'user', 'revision': revision, 'markdown': raw})
            record['document'].update(markdown=raw, revision=revision, polishing=True, polishedFrom=None, error=None)
            self._save(record)
            self._spawn(self._polish, task_id, record['token'], revision, text, previous, editing)
            return self.detail(task_id)

    def _settings_busy(self):
        if self._configuring or any(r['phase'] == 'retrieving' or r['document']['polishing'] for r in self._records.values()):
            return True
        if any(any(o['status'] == 'running' for o in app.operations) for app in self._apps.values()):
            return True
        return any(any(n['status'] == 'running' for n in app.engine.snapshot()['nodes']) or not app.engine.snapshot()['paused'] for app in self._apps.values())

    @staticmethod
    def _paper_context(record):
        paper = record['paper']
        return {'researchBudget': BUDGETS[paper.get('budgetTier', 'swarm')], 'selectedTopic': next((t for t in paper['topics'] if t['id'] == paper['selectedTopicId']), None),
                'sections': [{k: (s[k][:6000] if k == 'markdown' else s[k]) for k in ('id', 'markdown', 'author', 'evidenceIds')}
                             for s in paper['sections']], 'userContributions': paper['decisions'][-20:]}

    def _paper_post(self, task_id, action, payload):
        with self._lock:
            record = self._record(task_id)
            paper = record['paper']
            app = self._apps.get(task_id)
            if action == 'paper/budget':
                if record['phase'] not in ('empty', 'requirements'):
                    raise ValueError('运行中的研究请先暂停并回到需求准备，再修改规模')
                if payload.get('tier') not in BUDGETS:
                    raise ValueError('研究规模选项无效')
                paper['budgetTier'] = payload['tier']
                paper['revision'] += 1
                self._save(record)
                return self.detail(task_id)
            if action.startswith('paper/sections/'):
                section_id = action.split('/')[-1]
                edit_section(paper, section_id, payload)
                if app:
                    app.engine.command('paper-context', {'paperContext': self._paper_context(record)})
                self._save(record)
                return self.detail(task_id)
            if action == 'paper/topic':
                if record['phase'] not in ('empty', 'requirements') or record['document']['polishing'] or self._configuring:
                    raise ValueError('请先等待需求整理结束；研究中的方向调整请使用节点干预')
                if payload.get('expectedRevision') != paper['revision']:
                    raise ValueError('选题版本已变化，请重新查看')
                topic = next((t for t in paper['topics'] if t['id'] == payload.get('topicId')), None)
                if not topic:
                    raise ValueError('选题候选不存在')
                note = payload.get('note', '')
                if not isinstance(note, str) or len(note) > 5000:
                    raise ValueError('补充说明不能超过 5000 字符')
                paper['selectedTopicId'] = topic['id']
                paper['revision'] += 1
                paper['approvedRevision'] = None
                paper['decisions'].append({'id': identity(), 'kind': 'topic', 'title': '选定研究课题',
                    'answer': topic['title'] + ('；' + note if note else ''), 'at': utc_now(), 'actor': 'user',
                    'effect': '研究问题、初始假设和首个实验写入需求，后续节点据此工作。'})
                text = '我选择的论文课题：' + topic['title'] + '\n研究问题：' + topic['question'] + '\n待验证假设：' + topic['hypothesis'] + '\n首个实验：' + topic['firstExperiment'] + '\n我的补充：' + note
                return self._edit(task_id, {'text': text}, False)
            if action == 'paper/decision':
                if record['phase'] in ('empty', 'requirements', 'retrieving'):
                    raise ValueError('请先开始当前研究')
                if not app:
                    raise ValueError('请重新读取研究状态')
                before = app.engine.snapshot()
                question = before['project'].get('researchDecision')
                state = app.engine.command('research-choice', payload)
                choice = state['project']['researchChoices'][-1]
                paper['decisions'].append({'id': choice['id'], 'kind': 'research', 'title': question['question'],
                    'answer': choice['answer'], 'at': choice['at'], 'actor': 'user', 'nodeId': choice['nodeId'],
                    'effect': f'已更新 {len(choice["affectedIds"])} 个相关节点的研究输入，重新验证受影响的结果。'})
                paper['revision'] += 1
                paper['approvedRevision'] = None
                record['phase'], record['error'] = 'researching', None
                self._message(record, 'user', '我的研究决定：' + choice['answer'], 'decision')
                self._message(record, 'assistant', '你的选择已经进入研究输入。受影响分支会依据这个决定重新验证，论文保留已有内容并标注失效来源。', 'progress')
                self._save(record)
                return self.detail(task_id)
            if action == 'paper/approve':
                state = app.snapshot() if app else self.detail(task_id)['state']
                sync_paper(paper, state)
                if payload.get('expectedRevision') != paper['revision']:
                    raise ValueError('论文版本已变化，请重新审阅')
                if not any(s['markdown'].strip() for s in paper['sections']):
                    raise ValueError('论文还没有正文可确认')
                if state and state['project'].get('researchDecision'):
                    raise ValueError('请先处理当前研究决策')
                if paper_view(paper, state)['issues'] and payload.get('acknowledgeIssues') is not True:
                    raise ValueError('当前草稿仍有未决问题，请查看后确认')
                paper['sourceSignature'] = source_signature(paper, state)
                paper['approvedRevision'] = paper['revision']
                paper['decisions'].append({'id': identity(), 'kind': 'approval', 'title': '确认了当前论文草稿',
                    'answer': '已审阅 v' + str(paper['revision']), 'at': utc_now(), 'actor': 'user',
                    'effect': '确认此版本可作为交付草稿；不代表实验或创新性自动成立。'})
                self._save(record)
                return self.detail(task_id)
            raise ValueError('未知论文操作')

    def post(self, path, payload):
        # Long retrieval runs outside this transaction; its eventual engine commit
        # shares the same lock through ResearchApplication.mutation_lock.
        if path.endswith('/deepen'):
            return self._post(path, payload)
        with self._mutation_lock:
            return self._post(path, payload)

    def _post(self, path, payload):
        if path == '/api/tasks':
            with self._lock:
                return self.detail(self._create()['id'])
        if path in ('/api/setup', '/api/settings'):
            with self._lock:
                if self._settings_busy():
                    raise ValueError('请先暂停研究并等待需求整理结束，再修改模型连接')
                if path == '/api/setup':
                    key = payload.get('apiKey', '')
                    if not isinstance(key, str) or not key.strip():
                        raise ValueError('请输入 DeepSeek API Key')
                    payload = {'mode': 'llm', 'provider': {'type': 'openai', 'baseUrl': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'apiKey': key}}
                before = copy.deepcopy(self.settings.data)
                self._configuring = True
            try:
                self.settings.update(payload)
                if path == '/api/setup':
                    self.settings.chat([{'role': 'user', 'content': 'Reply only: OK'}], max_tokens=32)
                return dict(self.settings.public(), ok=True, message='DeepSeek 已连接，所有科研任务共用此连接')
            except Exception as exc:
                message = self.settings.safe_error(exc)
                self.settings.restore(before)
                raise ValueError(message) from None
            finally:
                with self._lock:
                    self._configuring = False
        if path == '/api/provider/test':
            self.settings.chat([{'role': 'user', 'content': 'Reply only: OK'}], max_tokens=32)
            return {'ok': True, 'message': '模型连接成功'}
        match = re.fullmatch(r'/api/tasks/([a-f0-9]{32})/(.+)', path)
        if not match:
            raise ValueError('未知科研任务操作')
        task_id, action = match.groups()
        with self._lock:
            self._record(task_id)
        if action in ('messages', 'document'):
            return self._edit(task_id, payload, action == 'document')
        if action.startswith('paper/'):
            return self._paper_post(task_id, action, payload)
        if action == 'start':
            with self._lock:
                record = self._record(task_id)
                if self._configuring:
                    raise ValueError('正在验证模型配置，请稍后再试')
                if payload.get('expectedRevision') != record['document']['revision']:
                    raise ValueError('需求文档版本已变化，请同步后开始')
                if record['document']['polishing'] or not record['compiled'] or record['document'].get('error'):
                    raise ValueError('请先完成需求文档整理，再开始研究')
                if record['phase'] in ('retrieving', 'researching'):
                    raise ValueError('本任务已经在研究中')
                if record['paper']['topics'] and not record['paper']['selectedTopicId']:
                    raise ValueError('请选择一个论文选题，再开始研究')
                record['token'] += 1
                record['phase'], record['error'] = 'retrieving', None
                self._message(record, 'user', '按当前需求开始研究。')
                self._save(record)
                self._spawn(self._run, task_id, record['token'])
                return self.detail(task_id)
        if action.startswith('actions/') or action == 'deepen':
            operation = action.removeprefix('actions/')
            if operation not in ('pause', 'resume', 'retry', 'impact', 'intervene', 'deepen', 'rollback'):
                raise ValueError('当前对话流程不支持此操作')
            with self._lock:
                record = self._record(task_id)
                task_token = record['token']
                if operation in ('resume', 'retry', 'intervene', 'deepen') and record['phase'] in ('empty', 'requirements', 'retrieving'):
                    raise ValueError('请先开始当前需求对应的研究')
            app = self._ensure_app(task_id)
            result = app.post('/api/' + action, payload)
            if operation == 'impact':
                return result
            with self._mutation_lock:
                with self._lock:
                    record = self._record(task_id)
                    if record['token'] != task_token:
                        return self.detail(task_id)
                    if operation == 'pause' and record['phase'] == 'retrieving':
                        record['token'] += 1
                        record['phase'] = 'requirements'
                        self._message(record, 'assistant', '已停止后续研究调度。进行中的论文请求结束后只保留资料。', 'progress')
                    elif operation != 'pause':
                        record['phase'], record['error'] = 'researching', None
                    if operation in ('intervene', 'deepen'):
                        record['paper']['decisions'].append({'id': identity(), 'kind': 'intervention',
                            'title': '你调整了研究任务', 'answer': payload.get('text', payload.get('query', '')),
                            'at': utc_now(), 'actor': 'user', 'nodeId': payload.get('nodeId'),
                            'effect': '受影响节点已重新调度；原输出保留在历史记录中。'})
                        record['paper']['revision'] += 1
                        record['paper']['approvedRevision'] = None
                    self._save(record)
                    return self.detail(task_id)
        raise ValueError('未知科研任务操作')

    def read_api(self, path, query=''):
        if path == '/api/tasks':
            return 200, {'tasks': self.tasks()}, 'application/json', None
        if path == '/api/settings':
            return 200, self.settings.public(), 'application/json', None
        if path == '/api/health':
            return 200, {'ok': True, 'service': 'research-swarm', 'version': '0.2.0'}, 'application/json', None
        match = re.fullmatch(r'/api/tasks/([a-f0-9]{32})(?:/(.*))?', path)
        if not match:
            return None
        task_id, action = match.groups()
        if task_id not in self._apps and (self._data_root / task_id / 'runtime' / 'swarm.sqlite').is_file():
            self._ensure_app(task_id)
        detail = self.detail(task_id)
        if not action:
            return 200, detail, 'application/json', None
        if action == 'document':
            return 200, detail['document']['markdown'].encode('utf-8'), 'text/markdown; charset=utf-8', {'Content-Disposition': 'attachment; filename="requirements.md"'}
        if action == 'paper/terminal':
            from urllib.parse import parse_qs
            execution_id = parse_qs(query).get('execution', [''])[0]
            entry = next((e for e in (detail['state'] or {}).get('history', [])
                          if e.get('type') in ('tool-executed', 'tool-started') and e.get('id') == execution_id), None)
            if not entry:
                raise ValueError('执行记录不存在')
            root = (self._data_root / task_id / 'runtime').resolve()
            result = {}
            for channel in ('stdout', 'stderr'):
                relative = entry['execution'].get(channel + 'Path')
                path = (root / str(relative or '')).resolve()
                if relative and path.is_relative_to(root / 'runs') and path.is_file():
                    with path.open('rb') as handle:
                        raw = handle.read(120001)
                    result[channel] = raw[:120000].decode('utf-8', errors='replace') + ('\n[输出已截断，请下载完整日志]' if len(raw) > 120000 else '')
                else:
                    result[channel] = ''
            return 200, result, 'application/json', None
        if action == 'paper/preview':
            from urllib.parse import parse_qs
            from .artifact_views import preview_artifact
            relative = parse_qs(query).get('path', [''])[0]
            root = self._data_root / task_id / 'runtime'
            registered = {a['url'].split('/artifacts/', 1)[1] for a in detail['artifacts'] if '/artifacts/' in a['url']}
            return 200, preview_artifact(root, relative, registered), 'application/json', None
        if action == 'paper/live-chart':
            from urllib.parse import parse_qs
            from .artifact_views import live_dashboard
            execution_id = parse_qs(query).get('execution', [''])[0]
            state = detail['state'] or {}
            entry = next((e for e in state.get('history', []) if e.get('type') == 'tool-started' and e.get('id') == execution_id), None)
            if not entry:
                raise ValueError('执行记录不存在')
            execution = entry['execution']
            node = next((n for n in state.get('nodes', []) if n['id'] == execution.get('nodeId')), {})
            finished = any(e.get('type') == 'tool-executed' and e.get('execution', {}).get('stdoutPath') == execution.get('stdoutPath')
                           for e in state.get('history', []))
            current = (not finished and node.get('active') and node.get('status') == 'running' and node.get('version') == execution.get('nodeVersion')
                       and state.get('project', {}).get('round') == execution.get('round'))
            if not current:
                return 200, {'pending': True}, 'application/json', None
            return 200, live_dashboard(self._data_root / task_id / 'runtime', execution), 'application/json', None
        if action in ('paper/manuscript', 'paper/export'):
            with self._lock:
                paper = copy.deepcopy(self._record(task_id)['paper'])
            files = export_paper(paper, detail['state'], detail['task']['title'])
            if action == 'paper/manuscript':
                return 200, files['paper/manuscript.md'].encode('utf-8'), 'text/markdown; charset=utf-8', {'Content-Disposition': 'attachment; filename="manuscript.md"'}
            state = detail['state']
            if state:
                app = self._apps.get(task_id)
                if app:
                    state['executionHistory'] = app.engine.export_audit()
                stream = io.BytesIO(export_bundle(state, self._data_root / task_id / 'runtime', allow_draft=True))
            else:
                stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'a', zipfile.ZIP_DEFLATED) as archive:
                for name, text in files.items():
                    archive.writestr(name, text.encode('utf-8'))
                archive.writestr('requirements.md', detail['document']['markdown'].encode('utf-8'))
                archive.writestr('conversation.json', json.dumps(detail['messages'], ensure_ascii=False, indent=2).encode('utf-8'))
            return 200, stream.getvalue(), 'application/zip', {'Content-Disposition': 'attachment; filename="paper-research.zip"'}
        if action.startswith('artifacts/'):
            root = (self._data_root / task_id / 'runtime').resolve()
            path = (root / action[len('artifacts/'):]).resolve()
            allowed = {a['url'] for a in detail['artifacts'] if a.get('kind') == 'experiment'}
            if f'/api/tasks/{task_id}/{action}' not in allowed or not path.is_relative_to(root/'runs') or not path.is_file():
                raise ValueError('找不到当前研究的已登记产物')
            from urllib.parse import quote
            return 200, path.read_bytes(), 'application/octet-stream', {'Content-Disposition': "attachment; filename*=UTF-8''"+quote(path.name)}
        if action.startswith('runs/'):
            index = int(action.split('/')[-1])
            path = self._data_root / task_id / 'reports' / f'round-{index}.json'
            if not path.is_file():
                raise ValueError('研究历史不存在')
            return 200, json.loads(path.read_text(encoding='utf-8')), 'application/json', None
        if action == 'export':
            state = detail['state']
            if detail['phase'] != 'completed' or not state or not state['report'].get('ready', state['report'].get('approved')):
                raise ValueError('研究尚未完成')
            app = self._apps.get(task_id)
            if app:
                state['executionHistory'] = app.engine.export_audit()
            data = export_bundle(state, self._data_root / task_id / 'runtime')
            stream = io.BytesIO(data)
            with zipfile.ZipFile(stream, 'a', zipfile.ZIP_DEFLATED) as archive:
                for name, text in export_paper(self._record(task_id)['paper'], state, detail['task']['title']).items():
                    archive.writestr(name, text.encode('utf-8'))
                archive.writestr('requirements.md', detail['document']['markdown'].encode('utf-8'))
                archive.writestr('conversation.json', json.dumps(detail['messages'], ensure_ascii=False, indent=2).encode('utf-8'))
                archive.writestr('requirement-history.json', json.dumps(self._record(task_id)['documentHistory'], ensure_ascii=False, indent=2).encode('utf-8'))
            return 200, stream.getvalue(), 'application/zip', {'Content-Disposition': 'attachment; filename="research-results.zip"'}
        if action.startswith('nodes/') and action.endswith('/history'):
            app = self._ensure_app(task_id)
            return 200, {'runs': app.engine.node_history(action[len('nodes/'):-len('/history')])}, 'application/json', None
        if action.startswith('papers/') and action.endswith('/pdf'):
            app = self._ensure_app(task_id)
            path = app.library.pdf_path(action.split('/')[1])
            if not path:
                raise ValueError('这篇论文暂无可验证的本地 PDF')
            return 200, Path(path).read_bytes(), 'application/pdf', {'Content-Disposition': 'inline; filename="paper.pdf"'}
        return None

    def close(self):
        with self._lock:
            if self._closed:
                return
            for record in self._records.values():
                self._synchronize(record)
            self._closed = True
            self._stop_event.set()
            for app in self._apps.values():
                app.engine.close()
        for thread in self._threads:
            thread.join(timeout=.05)
        self._supervisor.join(timeout=.2)
