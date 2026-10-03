"""Persistent gated or autonomous research workflows with bounded execution."""

import copy
import hashlib
import json
import threading
import time
import traceback
import uuid
from pathlib import Path
from datetime import datetime, timezone

from .store import Store
from .claims import ensure_graph, get_claim, preserve_history


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _id(prefix):
    return prefix + ":" + uuid.uuid4().hex


def _text(value):
    if isinstance(value, list):
        return "；".join(str(item) for item in value)
    return str(value or "").strip()


def _unique(values):
    return list(dict.fromkeys(str(value) for value in values))


class Engine:
    MAX_DEPTH = 4
    MAX_TASKS = 80
    MAX_ITERATIONS = 3
    CHECKPOINTS = {
        "requirements": (0, "确认研究需求", "请确认研究范围、约束和验收标准。"),
        "recommendations": (3, "筛选推荐论文", "请检查推荐论文与已有证据，确认本轮研究范围。"),
        "comparison": (7, "确认对比结论", "以下为候选研究判断，请核对证据和未决问题。"),
        "final": (8, "确认最终输出", "请确认报告内容；确认后才允许导出。"),
    }

    def __init__(self, library, store_path, runner=None, max_workers=3, workflow="gated"):
        if not isinstance(library, dict):
            raise ValueError("论文库必须是结构化对象")
        if workflow not in ("gated", "autonomous"):
            raise ValueError("研究流程必须为 gated 或 autonomous")
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= 16:
            raise ValueError("并行任务数量必须在 1 到 16 之间")
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._closed = False
        self._closing = False
        self._manual_paused = False
        self._runner = runner
        self._artifact_root = Path(store_path).resolve().parent
        self._cycle_now = _now
        self._store = Store(store_path)
        self._library = copy.deepcopy(library)
        self._executions = {}
        self._local_tokens = {}
        self._new_checkpoints = []
        self._new_outputs = []
        self._invalidations = []
        stored = self._store.load()
        if stored:
            self._state = stored["state"]
            self._state["project"].setdefault("workflow", workflow)
            self._state["project"].setdefault("researchStarted", self._state["stage"] >= 4)
            self._state["project"].setdefault("researchIteration", 1 if self._state["stage"] >= 4 else 0)
            self._state["report"].setdefault("ready", self._state["report"]["approved"])
            self._state["report"].setdefault("structured", {})
            self._library = stored.get("library") or self._library
            self._epoch = int(stored.get("epoch", 0)) + 1
            self._state["paused"] = True
            self._manual_paused = not (self._state["report"]["approved"] or self._state["report"].get("ready"))
            for node in self._state["nodes"]:
                if (node.get('modelWait') or {}).get('nextRetryAt'):
                    node['retryNotBefore'] = node['modelWait']['nextRetryAt']
                node.pop('modelWait', None)
                if node["status"] == "running":
                    node["status"] = "pending"
                    node["progress"] = 0
                    node["version"] += 1
                    node["startedAt"] = None
            self._activity("system", "已从本机数据库恢复；保持暂停，等待用户继续。")
            self._recover_tool_receipts(Path(store_path).parent)
            self._commit()
        else:
            self._epoch = 0
            self._state = self._initial_state(library, workflow)
            if not self._autonomous():
                self._create_checkpoint("requirements")
            self._activity("system", "已导入论文、证据和全部切面节点代理。" +
                           ("编辑需求后即可开始研究。" if self._autonomous() else "等待需求确认。"))
            self._commit()
        self._workers = [threading.Thread(target=self._worker, daemon=True,
                                          name=f"research-worker-{index + 1}")
                         for index in range(max_workers)]
        for worker in self._workers:
            worker.start()

    def _initial_state(self, library, workflow):
        topic = library.get("topic") or {}
        description = _text(topic.get("description")) or _text(topic.get("title")) or "新研究课题"
        acceptance = _text(topic.get("successCriteria")) or "每项结论提供可定位证据，并明确资料局限与未决问题"
        state = {
            "revision": 0, "project": {"id": str(topic.get("id") or _id("project")),
                "title": _text(topic.get("title")) or "科研蜂群", "description": description,
                "round": 1, "sourcePath": str(library.get("sourcePath") or ""), "mode": "evidence",
                "workflow": workflow, "researchStarted": False, "researchIteration": 0},
            "stage": 0, "paused": True, "status": "waiting_user",
            "requirements": [{"id": "requirement:1", "description": description,
                              "acceptance": acceptance, "constraints": "不将摘要推断表述为全文或实验验证", "version": 1}],
            "nodes": [], "edges": [], "papers": copy.deepcopy(library.get("papers", [])),
            "facetNodes": copy.deepcopy(library.get("facetNodes", [])),
            "evidence": copy.deepcopy(library.get("evidence", [])), "checkpoints": [],
            "activeCheckpointId": None, "activities": [],
            "report": self._empty_report(), "history": [],
        }
        state["nodes"].append(self._node("central", None, "中央研究代理", "research", None,
                                           {"description": description, "acceptance": acceptance}, True))
        for facet in state["facetNodes"]:
            source = str(facet["id"])
            parent = str(facet["parentId"]) if facet.get("parentId") is not None else None
            node = self._node("facet:" + source, "facet:" + parent if parent else None,
                              _text(facet.get("title")) or source, "research", source,
                              {"description": _text(facet.get("title")), "acceptance": acceptance,
                               "constraints": "", "paperIds": copy.deepcopy(facet.get("paperIds", [])),
                               "facetId": facet.get("facetId"), "facetName": facet.get("facetName")}, False)
            state["nodes"].append(node)
        for node in state["nodes"]:
            node["requirementIds"] = ["requirement:1"]
        return state

    @staticmethod
    def _empty_report():
        return {"summary": "", "claims": [], "unresolved": [], "structured": {}, "approved": False, "ready": False}

    def _autonomous(self):
        return self._state["project"].get("workflow", "gated") == "autonomous"

    def _limit(self, name):
        default = {'maxTasks': self.MAX_TASKS, 'maxIterations': self.MAX_ITERATIONS, 'maxDepth': self.MAX_DEPTH,
                   'maxParallel': len(self._workers) if hasattr(self, '_workers') else 3}[name]
        return self._state['project'].get('researchBudget', {}).get(name, default)

    def _node(self, node_id, parent_id, title, kind, source_id, input_data, active):
        return {"id": node_id, "parentId": parent_id, "title": title,
                "role": "中央研究代理" if node_id == "central" else "切面研究代理" if source_id else "研究任务代理",
                "kind": kind, "phase": "plan" if kind == "research" else "execute",
                "status": "pending", "progress": 0, "input": copy.deepcopy(input_data),
                "output": None, "logs": [], "sourceNodeId": source_id,
                "requirementIds": [], "evidenceIds": [], "startedAt": None,
                "finishedAt": None, "elapsedMs": 0, "version": 1, "active": active}

    def snapshot(self):
        with self._lock:
            result = copy.deepcopy(self._state)
            for node in result["nodes"]:
                if node['status'] == 'failed' and not node.get('error'):
                    entry = next((entry for entry in reversed(node.get('logs', [])) if entry.get('level') == 'error'), {})
                    node['error'] = {'message': entry.get('message', '执行失败，查看历史执行'), 'code': 'execution_failed', 'at': entry.get('at')}
                token = self._executions.get(node["id"])
                if token and node["status"] == "running":
                    node["elapsedMs"] += max(0, int((time.monotonic() - token["started"]) * 1000))
            # Comparison is a view relation, never a scheduling/invalidation dependency.
            by_id = {node["id"]: node for node in result["nodes"]}
            siblings = {}
            for node in result["nodes"]:
                if node["active"] and node["parentId"] is not None:
                    siblings.setdefault(node["parentId"], []).append(node["id"])
            compared = {frozenset((edge["source"], edge["target"]))
                        for edge in result["edges"] if edge["type"] == "compare"}
            for parent_id, children in siblings.items():
                parent_title = by_id[parent_id]["title"]
                for index, source in enumerate(children):
                    for target in children[index + 1:]:
                        pair = frozenset((source, target))
                        if pair not in compared:
                            result["edges"].append({"source": source, "target": target, "type": "compare",
                                                    "reason": f"同一上级需求「{parent_title}」下的并列研究任务。",
                                                    "informational": True})
                            compared.add(pair)
            return result

    def node_history(self, node_id=None):
        """Read immutable accepted executions, including nodes archived in earlier rounds."""
        with self._lock:
            if self._closed:
                raise ValueError("研究引擎已关闭")
            records = self._store.node_history(node_id)
            records.extend(dict(entry, valid=False, output=None) for entry in self._state['history']
                           if entry.get('type') == 'execution-failed' and (node_id is None or entry.get('nodeId') == node_id))
            records.extend(copy.deepcopy(entry) for entry in self._state['history']
                           if entry.get('type') == 'tool-executed' and (node_id is None or entry.get('nodeId') == node_id))
            diagnostics = {entry['executionToken']: entry for entry in self._state['history']
                           if entry.get('type') == 'model-diagnostics'}
            for record in records:
                diagnostic = diagnostics.get(record.get('executionToken', record['id']))
                if diagnostic:
                    record.update({key: copy.deepcopy(diagnostic[key]) for key in ('diagnosticRef', 'attemptCount', 'validationErrors')})
            return sorted(records, key=lambda item: item.get('at', ''))

    def export_audit(self):
        return self.node_history(None)

    def command(self, action, payload):
        if not isinstance(payload, dict):
            raise ValueError("操作参数必须是对象")
        with self._condition:
            if self._closed:
                raise ValueError("研究引擎已关闭")
            operation_id = payload.get('operationId')
            operation_hash = None
            if operation_id is not None:
                if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 100:
                    raise ValueError('操作标识无效')
                operation_hash = hashlib.sha256(json.dumps({'action': action,
                    'payload': {k: v for k, v in payload.items() if k != 'expectedRevision'}},
                    sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
                applied = self._state.get('appliedOperations', {}).get(operation_id)
                if applied:
                    if applied['hash'] != operation_hash:
                        raise ValueError('操作标识已用于不同请求')
                    return self.snapshot()
            if action == 'draft-impact':
                from .requirement_changes import impact
                return impact(self, payload.get('requirements'))
            if action == "impact":
                node = self._action_node(payload)
                kind = self._kind(payload.get("kind"))
                affected = self._affected(node["id"], kind)
                return {"revision": self._state["revision"], "affectedIds": affected,
                        "downstreamCount": len(affected) - 1}
            methods = {
                "requirements": self._requirements, "checkpoint": self._checkpoint,
                "pause": self._pause, "resume": self._resume, "intervene": self._intervene,
                "rollback": self._rollback_command, "feedback": self._feedback,
                "retry": self._retry, "mode": self._mode, "next-round": self._next_round,
                "refresh-library": self._refresh_library,
                "start-autonomous": self._start_autonomous,
                "research-choice": self._research_choice,
                "topic-selection": self._topic_selection,
                "topic-refresh": self._topic_refresh,
                "paper-context": self._paper_context,
                "claim-decision": self._claim_decision,
                "revise-requirements": self._revise_requirements,
            }
            if action not in methods:
                raise ValueError("不支持的操作: " + str(action))
            before = copy.deepcopy(self._state)
            old_epoch = self._epoch
            old_library = self._library
            old_manual_paused = self._manual_paused
            old_executions = dict(self._executions)
            try:
                methods[action](copy.deepcopy(payload))
                if operation_id:
                    self._state.setdefault('appliedOperations', {})[operation_id] = {
                        'hash': operation_hash, 'action': action, 'at': _now()}
                self._commit()
            except Exception:
                self._state = before
                self._epoch = old_epoch
                self._library = old_library
                self._manual_paused = old_manual_paused
                self._executions = old_executions
                self._new_checkpoints.clear()
                self._new_outputs.clear()
                self._invalidations.clear()
                raise
            self._condition.notify_all()
            return self.snapshot()

    def _action_node(self, payload):
        if payload.get('claimId'):
            claim = get_claim(self._state, payload['claimId'])
            if claim.get('archived') or not claim.get('ownerNodeId'):
                raise ValueError('该主张属于历史记录；请从当前研究节点发起新的取证任务')
            if payload.get('nodeId') and payload['nodeId'] != claim['ownerNodeId']:
                raise ValueError('主张与操作节点不匹配')
            return self._get_node(claim['ownerNodeId'])
        return self._get_node(payload.get('nodeId'))

    def _revise_requirements(self, payload):
        from .requirement_changes import apply
        apply(self, payload)

    def _claim_decision(self, payload):
        if payload.get('expectedRevision') != self._state['revision']:
            raise ValueError('状态版本已变化，请重新查看主张再确认')
        claim = get_claim(self._state, payload.get('claimId'))
        if claim.get('archived') or payload.get('decision') != 'confirm':
            raise ValueError('只能确认当前主张；修改或否决请先预览影响范围')
        if claim['assessment']['status'] == 'unassessed':
            raise ValueError('主张尚未论证，不能确认研究判断')
        acknowledgement = self._responsibility(payload)
        claim['assessment']['confirmedByUser'] = True
        decision = {'actor': 'user', 'decision': 'confirm', 'version': claim['version'],
                    'reason': _text(payload.get('note')), 'at': _now(), **acknowledgement}
        claim.setdefault('decisions', []).append(decision)
        for row in self._state['report']['claims']:
            if row.get('claimId') == claim['id'] and row.get('claimVersion') == claim['version']:
                row['status'] = 'confirmed'
        self._history('claim-decision', claimId=claim['id'], **decision)
        self._activity('user', '已确认主张判断：' + claim['statement'])

    @staticmethod
    def _responsibility(payload):
        name = payload.get('responsibilityName')
        if payload.get('responsibilityAcknowledged') is not True or not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise ValueError('确认前请签名并明确知悉：确认记录只代表个人判断，不构成科学验证或已阅读证明')
        return {'responsibilityAcknowledged': True, 'responsibilityName': name.strip(),
                'acknowledgementVersion': 1, 'scientificValidation': False}

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._stop_running("引擎关闭")
            self._state["paused"] = True
            self._activity("system", "研究引擎已安全暂停；未完成执行将在恢复后重新运行。")
            self._commit()
            self._closed = True
            self._closing = True
            self._condition.notify_all()
            # Drain only local processes being cancelled, never a model network
            # request. Late atomic receipts are recoverable on the next startup.
            deadline = time.monotonic()+3
            while self._local_tokens and time.monotonic()<deadline:
                self._condition.wait(timeout=max(0, deadline-time.monotonic()))
            self._closing = False
            self._store.close()
        # Runners may be blocked on a provider. Daemon workers cannot write after close.
        for worker in self._workers:
            worker.join(timeout=0.05)

    def _get_node(self, node_id):
        for node in self._state["nodes"]:
            if node["id"] == node_id:
                return node
        raise ValueError("找不到该研究节点")

    def _children(self, node_id):
        return [node for node in self._state["nodes"] if node["active"] and node["parentId"] == node_id]

    def _activity(self, actor, message, node=None, level="info"):
        entry = {"id": _id("activity"), "at": _now(), "actor": actor,
                 "message": str(message), "level": level}
        if node is not None:
            entry["nodeId"] = node["id"]
            node["logs"].append(entry)
        self._state["activities"].append(entry)
        return entry

    def _history(self, event_type, **details):
        self._state["history"].append({"id": _id("history"), "at": _now(), "type": event_type,
                                      "round": self._state["project"]["round"], **details})

    def _refresh_status(self):
        from .topic_selection import pending
        state = self._state
        from .execution_status import summarize
        summary = summarize(state, self._node_ready)
        state['executionSummary'] = summary
        if state["activeCheckpointId"] or pending(state['project']):
            state["status"] = "waiting_user"
            state["paused"] = True
        elif state["report"]["approved"] or state["report"].get("ready"):
            state["status"] = "completed"
        elif summary['failed'] and not (summary['running'] or summary['waitingProvider'] or summary['ready']):
            state["status"] = "failed"
        elif state["paused"]:
            state["status"] = "idle"
        else:
            state["status"] = "running"

    def _commit(self):
        ensure_graph(self._state)
        self._state["revision"] += 1
        self._refresh_status()
        self._refresh_paper_statuses()
        self._store.save(self._state, self._epoch, library=self._library, manual_paused=self._manual_paused,
                         checkpoints=self._new_checkpoints,
                         outputs=self._new_outputs, invalidations=self._invalidations)
        self._new_checkpoints.clear()
        self._new_outputs.clear()
        self._invalidations.clear()

    def _refresh_paper_statuses(self):
        statuses = {}
        facets = {str(facet["id"]): facet for facet in self._state["facetNodes"]}
        for node in self._state["nodes"]:
            if not node["active"] or node["kind"] not in ("evidence", "experiment") or self._children(node["id"]):
                continue
            paper_ids = node["input"].get("paperIds") or []
            if not paper_ids and node["kind"] == "evidence" and node["sourceNodeId"]:
                paper_ids = facets.get(node["sourceNodeId"], {}).get("paperIds", [])
            for paper_id in paper_ids:
                statuses.setdefault(str(paper_id), []).append(node["status"])
        for paper in self._state["papers"]:
            tasks = statuses.get(str(paper["id"]), [])
            paper["workerStatus"] = "completed" if tasks and all(status == "completed" for status in tasks) else "pending"
            for status in ("running", "failed", "waiting_user", "pending"):
                if status in tasks:
                    paper["workerStatus"] = status
                    break

    def _create_checkpoint(self, kind):
        stage, title, summary = self.CHECKPOINTS[kind]
        checkpoint = {"id": _id("checkpoint"), "type": kind, "status": "pending",
                      "title": title, "summary": summary, "createdAt": _now(),
                      "revision": self._state["revision"] + 1,
                      "round": self._state["project"]["round"]}
        self._state["checkpoints"].append(checkpoint)
        self._state["activeCheckpointId"] = checkpoint["id"]
        self._state["stage"] = stage
        self._state["paused"] = True
        self._new_checkpoints.append(checkpoint["id"])

    def _validated_requirements(self, values):
        if not isinstance(values, list) or not values or len(values) > 80:
            raise ValueError("请填写 1 至 80 项研究需求")
        previous = {value["id"]: value for value in self._state["requirements"]}
        facet_ids = {str(facet["id"]) for facet in self._state["facetNodes"]}
        used = set()
        result = []
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("每项需求必须包含描述与验收标准")
            item = copy.deepcopy(value)
            item["id"] = str(item.get("id") or _id("requirement"))
            if item["id"] in used:
                raise ValueError("需求 ID 不能重复")
            used.add(item["id"])
            for key in ("description", "acceptance"):
                item[key] = _text(item.get(key))
                if not item[key]:
                    raise ValueError("研究需求的描述和验收标准不能为空")
            item["constraints"] = _text(item.get("constraints"))
            if "sourceNodeIds" in item:
                if not isinstance(item["sourceNodeIds"], list):
                    raise ValueError("研究范围必须是切面节点 ID 列表")
                item["sourceNodeIds"] = _unique(item["sourceNodeIds"])
                if set(item["sourceNodeIds"]) - facet_ids:
                    raise ValueError("研究范围包含未知切面节点")
            old = previous.get(item["id"])
            item["version"] = int(old["version"]) if old else 1
            if old and any(item.get(key) != old.get(key) for key in ("description", "acceptance", "constraints", "sourceNodeIds")):
                item["version"] += 1
            result.append(item)
        return result

    def _requirements(self, payload):
        current = self._pending_checkpoint()
        autonomous_draft = self._autonomous() and not self._state["project"].get("researchStarted")
        if not autonomous_draft and (not current or current["type"] != "requirements"):
            raise ValueError("已确认的需求请先查看干预影响，再提交修改")
        self._state["requirements"] = self._validated_requirements(payload.get("requirements"))
        root = self._get_node("central")
        root["requirementIds"] = [item["id"] for item in self._state["requirements"]]
        for key in ("description", "acceptance", "constraints"):
            root["input"][key] = "；".join(item[key] for item in self._state["requirements"])
        self._activity("user", "已保存研究需求草稿及验收标准。")
        self._history("requirements", requirements=copy.deepcopy(self._state["requirements"]))

    def _start_autonomous(self, payload):
        if (self._state["project"].get("researchStarted") or self._state["report"].get("ready")
                or self._state["report"]["approved"] or any(node["active"] and (node["status"] == "running"
                or node["output"] is not None) for node in self._state["nodes"])):
            raise ValueError("本轮研究已开始；暂停后可继续，失败可重试，新需求请先准备下一轮")
        requirements = self._validated_requirements(payload.get("requirements", self._state["requirements"]))
        mode = payload.get("mode", self._state["project"]["mode"])
        if mode not in ("evidence", "llm"):
            raise ValueError("执行模式无效")
        task_mode = payload.get('taskMode', self._state['project'].get('taskMode', 'research'))
        if task_mode not in ('research', 'reproduction'):
            raise ValueError('科研任务类型必须为 research 或 reproduction')
        allow_search = payload.get("allowNewSearch", True)
        if not isinstance(allow_search, bool):
            raise ValueError("补充检索设置必须是布尔值")
        if "title" in payload and not _text(payload["title"]):
            raise ValueError("研究任务名称不能为空")
        self._stop_running("开始自主研究")
        self._state["project"].update(workflow="autonomous", mode=mode, researchStarted=True, researchIteration=1)
        self._state['project']['taskMode'] = task_mode
        self._state['project']['topicMode'] = payload.get('topicMode') if payload.get('topicMode') in ('explore', 'direct', 'delegate') else 'explore'
        self._state['project']['topicIntent'] = copy.deepcopy(payload.get('topicIntent') or {})
        self._state['project']['paperResearch'] = bool(payload.get('paperResearch'))
        self._state['project']['paperContext'] = copy.deepcopy(payload.get('paperContext') or {})
        if payload.get('budgetTier') is not None:
            from .research_contracts import BUDGETS
            if payload['budgetTier'] not in BUDGETS:
                raise ValueError('研究规模选项无效')
            self._state['project']['researchBudget'] = {**BUDGETS[payload['budgetTier']],
                'maxParallel': min(BUDGETS[payload['budgetTier']]['maxParallel'], len(self._workers))}
        self._state['project']['researchDecision'] = None
        if "title" in payload:
            self._state["project"]["title"] = _text(payload["title"])
        self._state["requirements"] = requirements
        self._state['project']['description'] = '；'.join(item['description'] for item in requirements)
        self._state["report"] = self._empty_report()
        root = self._get_node("central")
        root["input"] = {key: "；".join(item[key] for item in requirements)
                         for key in ("description", "acceptance", "constraints")}
        root["input"]["allowNewSearch"] = allow_search
        if payload.get("searchBudgetId") is not None:
            budget = _text(payload["searchBudgetId"])
            if not budget:
                raise ValueError("检索预算标识不能为空")
            root["input"]["searchBudgetId"] = budget
        if "markdown" in payload:
            root["input"]["sourceMarkdown"] = str(payload["markdown"])
        root["requirementIds"] = [item["id"] for item in requirements]
        root.update(active=True, status="pending", phase="plan", output=None, progress=0,
                    evidenceIds=[], startedAt=None, finishedAt=None)
        for checkpoint in self._state["checkpoints"]:
            if checkpoint["status"] == "pending":
                checkpoint["status"] = "superseded"
        # An already-resolved start snapshot provides rollback without a user-facing gate.
        self._create_checkpoint("requirements")
        checkpoint = self._state["checkpoints"][-1]
        checkpoint.update(status="confirmed", decision="start", resolvedAt=_now(), automatic=True,
                          title="自主研究起点", summary="用户已开始本轮研究；此快照供回滚追溯。")
        self._state["activeCheckpointId"] = None
        self._state["stage"] = 4
        self._state["paused"] = False
        self._manual_paused = False
        self._history("autonomous-start", requirements=copy.deepcopy(requirements),
                      mode=mode, searchBudgetId=root["input"].get("searchBudgetId"))
        self._activity("user", "已开始自主研究：将自动分解、执行、补充研究并整理候选报告。")
        if payload.get('researchCycle'):
            from .research_cycle import start
            start(self)
        else:
            self._state['project'].pop('researchCycle', None)

    def _pending_checkpoint(self):
        current_id = self._state["activeCheckpointId"]
        return next((item for item in self._state["checkpoints"]
                     if item["id"] == current_id and item["status"] == "pending"), None)

    def _checkpoint(self, payload):
        checkpoint = self._pending_checkpoint()
        if not checkpoint or checkpoint["id"] != payload.get("id"):
            raise ValueError("该检查点已变化或已处理，请刷新后操作")
        decision = payload.get("decision")
        if decision not in ("confirm", "modify", "rollback"):
            raise ValueError("检查点决策无效")
        note = _text(payload.get("note"))
        if decision == "rollback":
            index = self._state["checkpoints"].index(checkpoint)
            earlier = [item for item in self._state["checkpoints"][:index]
                       if item["status"] != "superseded"]
            self._rollback(earlier[-1]["id"] if earlier else checkpoint["id"])
            return
        if decision == "modify":
            checkpoint["decision"] = decision
            checkpoint["userNote"] = note
            self._state["paused"] = True
            self._activity("user", "要求修改检查点内容" + ("：" + note if note else "。"))
            self._history("checkpoint-modify", checkpointId=checkpoint["id"], note=note)
            return
        acknowledgement = self._responsibility(payload)
        if payload.get('expectedRevision', self._state['revision']) != self._state['revision']:
            raise ValueError('状态版本已变化，请重新查看检查点再确认')
        kind = checkpoint["type"]
        if kind == "requirements":
            self._state["requirements"] = self._validated_requirements(self._state["requirements"])
        checkpoint.update(status="confirmed", decision="confirm", userNote=note, resolvedAt=_now(), **acknowledgement)
        self._state["activeCheckpointId"] = None
        self._activity("user", "已确认：" + checkpoint["title"] + ("；" + note if note else ""))
        self._history("checkpoint-confirm", checkpointId=checkpoint["id"], checkpointType=kind, note=note, **acknowledgement)
        if kind == "requirements":
            self._state["stage"] = 1
            self._activity("system", f"已检索当前本机论文库：{len(self._state['papers'])} 篇论文，{len(self._state['evidence'])} 条证据。")
            self._state["stage"] = 2
            self._activity("system", f"已关联 {len(self._state['facetNodes'])} 个切面节点代理，等待推荐筛选。")
            self._create_checkpoint("recommendations")
        elif kind == "recommendations":
            self._state["stage"] = 4
            self._state["paused"] = False
            self._manual_paused = False
            self._state["project"].update(researchStarted=True, researchIteration=1)
            root = self._get_node("central")
            root["active"] = True
            if root["status"] == "completed":
                root["status"] = "pending"
                root["phase"] = "aggregate" if self._children(root["id"]) else "plan"
            self._activity("system", "推荐筛选已确认，开始本轮研究任务。")
        elif kind == "comparison":
            for claim in self._state["report"]["claims"]:
                if claim["status"] != "rejected":
                    claim["status"] = "confirmed"
            self._create_checkpoint("final")
        elif kind == "final":
            self._state["report"]["approved"] = True
            self._state["report"]["ready"] = True
            self._state["paused"] = True
            self._activity("system", "最终报告已获用户确认，可导出报告和追溯材料。")

    def _stop_running(self, cause):
        self._epoch += 1
        for node in self._state["nodes"]:
            if (node.get('modelWait') or {}).get('nextRetryAt'):
                node['retryNotBefore'] = node['modelWait']['nextRetryAt']
            node.pop('modelWait', None)
            if node["status"] == "running":
                token = self._executions.get(node["id"])
                if token:
                    node["elapsedMs"] += int((time.monotonic() - token["started"]) * 1000)
                node["version"] += 1
                node["status"] = "pending"
                node["progress"] = 0
                node["startedAt"] = None
                self._invalidations.append((node["id"], cause))
        self._executions.clear()

    def _pause(self, payload):
        self._stop_running("用户暂停")
        self._manual_paused = True
        self._state["paused"] = True
        self._activity("user", "已暂停任务派发；未完成执行的迟到结果将被废弃。")

    def _resume(self, payload):
        from .topic_selection import pending
        if pending(self._state['project']):
            raise ValueError('请先选择或自定义研究课题，继续按钮不能代替选题')
        if self._state['project'].get('researchDecision'):
            raise ValueError('当前有研究决策等待你选择，继续按钮不能代替你的判断')
        self._manual_paused = False
        if self._autonomous() and not self._state["project"].get("researchStarted"):
            self._state["paused"] = True
            self._activity("system", "当前仍在准备需求；请点击开始研究。")
        elif self._pending_checkpoint():
            self._state["paused"] = True
            self._activity("system", "当前检查点仍需用户确认，继续按钮不会代替确认。")
        elif self._state["report"]["approved"] or self._state["report"].get("ready"):
            self._state["paused"] = True
        else:
            self._state["paused"] = False
            self._activity("user", "已继续本轮研究任务。")

    def _paper_context(self, payload):
        self._state['project']['paperContext'] = copy.deepcopy(payload.get('paperContext') or {})
        if payload.get('supersedeDecision'):
            cycle = self._state['project'].get('researchCycle') or {}
            if (cycle.get('topicSelection') or {}).get('status') == 'pending':
                self._history('topic-selection-superseded', selection=copy.deepcopy(cycle['topicSelection']))
                cycle.update(topicSelection=None, topicCandidates=[], topic=None)
        if payload.get('supersedeDecision') and self._state['project'].get('researchDecision'):
            self._history('research-decision-superseded', decision=copy.deepcopy(self._state['project']['researchDecision']), reason='用户重新编辑了研究需求')
            self._state['project']['researchDecision'] = None

    def _research_choice(self, payload):
        question = self._state['project'].get('researchDecision')
        if not question or payload.get('decisionId') != question['id']:
            raise ValueError('研究决策已变化，请重新查看')
        index = payload.get('optionIndex')
        note = payload.get('note', '')
        if not isinstance(note, str) or len(note) > 8000:
            raise ValueError('补充说明不能超过 8000 字符')
        if index is not None and (type(index) is not int or not 0 <= index < len(question['options'])):
            raise ValueError('请选择有效的研究方向')
        if index is None and not note.strip():
            raise ValueError('请选择一个方向或填写自己的判断')
        option = question['options'][index] if index is not None else {'label': '我的判断', 'effect': ''}
        answer = option['label'] + '：' + option['effect'] + ('\n' + note.strip() if note.strip() else '')
        node = self._get_node(question['nodeId'])
        affected = self._affected(node['id'], 'modify')
        if node['input'].get('researchStep') == 'topic' and self._state['project'].get('researchCycle'):
            if payload.get('expectedRevision') != self._state['revision']:
                raise ValueError('状态版本已变化，请重新查看影响预览后提交')
            self._state['project']['researchCycle']['topic']['userChoice'] = answer
            self._activity('user', '已确定课题研究重点：' + answer, node)
        else:
            self._intervene({'nodeId': node['id'], 'kind': 'modify', 'expectedRevision': payload.get('expectedRevision'),
                             'text': node['input'].get('description', node['title']) + '\n用户已决定：' + answer})
        choice = {**copy.deepcopy(question), 'answer': answer, 'at': _now(), 'actor': 'user', 'affectedIds': affected}
        self._state['project'].setdefault('researchChoices', []).append(choice)
        self._state['project']['researchDecision'] = None
        self._history('research-choice', **choice)
        self._resume({})

    def _topic_selection(self, payload):
        from .topic_selection import select
        select(self, payload)

    def _topic_refresh(self, payload):
        from .topic_selection import pending
        if not pending(self._state['project']):
            raise ValueError('当前没有待调整的候选课题')
        cycle = self._state['project']['researchCycle']
        node = self._get_node(cycle['topicSelection']['nodeId'])
        previous = copy.deepcopy(cycle['topicCandidates'])
        feedback = payload.get('text')
        if not isinstance(feedback, str) or not 1 <= len(feedback.strip()) <= 8000:
            raise ValueError('请说明候选课题需要怎样调整')
        self._intervene({'nodeId': node['id'], 'kind': 'modify', 'expectedRevision': payload.get('expectedRevision'),
                         'text': '请根据已有文献重新提出候选课题。用户反馈：' + feedback})
        node['input']['previousTopicCandidates'] = previous
        cycle.update(status='running', stage='topic')
        self._resume({})

    def _kind(self, kind):
        if kind not in ("modify", "insert", "reject", "deepen"):
            raise ValueError("干预类型无效")
        return kind

    def _affected(self, node_id, kind="modify"):
        if self._state['project'].get('researchCycle') and kind in ('insert', 'deepen'):
            return ['central']
        affected = {node_id}
        if kind in ("modify", "reject"):
            queue = [node_id]
            while queue:
                parent = queue.pop()
                for child in self._children(parent):
                    if child["id"] not in affected:
                        affected.add(child["id"])
                        queue.append(child["id"])
                for consumer in self._state['nodes']:
                    if parent in consumer['input'].get('dependsOn', []) and consumer['id'] not in affected:
                        affected.add(consumer['id']); queue.append(consumer['id'])
        # Never traverse down again after adding an aggregation consumer: siblings stay valid.
        queue = list(affected)
        while queue:
            source = queue.pop()
            consumers = [edge["target"] for edge in self._state["edges"]
                         if edge["source"] == source and edge["type"] in ("return", "compare")]
            node = self._get_node(source)
            if node["active"] and node["parentId"]:
                consumers.append(node["parentId"])
            elif not node["active"] and source == node_id:
                consumers.append(self._activation_parent(node))
            for consumer in consumers:
                if consumer not in affected:
                    affected.add(consumer)
                    queue.append(consumer)
        return [node["id"] for node in self._state["nodes"] if node["id"] in affected]

    def _activation_parent(self, node):
        """An unselected source facet joins its nearest already-active source ancestor."""
        facets = {str(item["id"]): item for item in self._state["facetNodes"]}
        source = node["sourceNodeId"]
        seen = set()
        while source in facets and source not in seen:
            seen.add(source)
            parent = facets[source].get("parentId")
            if parent is None:
                break
            source = str(parent)
            agent = next((item for item in self._state["nodes"]
                          if item["id"] == "facet:" + source and item["active"]), None)
            if agent:
                return agent["id"]
        return "central"

    def _invalidate(self, node_ids, cause, *, supersede_decision=False):
        cycle = self._state['project'].get('researchCycle') or {}
        selection = cycle.get('topicSelection') or {}
        if supersede_decision and selection.get('nodeId') in node_ids:
            self._history('topic-selection-superseded', selection=copy.deepcopy(selection), reason=cause)
            cycle.update(topicCandidates=[], topicSelection=None, topic=None)
        from .claim_runtime import invalidate_claims
        invalidate_claims(self._state, set(node_ids), cause)
        decision = self._state['project'].get('researchDecision')
        if supersede_decision and decision and decision.get('nodeId') in node_ids:
            self._history('research-decision-superseded', decision=copy.deepcopy(decision), reason=cause)
            self._state['project']['researchDecision'] = None
        from .research_cycle import invalidate
        invalidate(self, set(node_ids))
        for node_id in node_ids:
            node = self._get_node(node_id)
            if node['input'].get('superseded'): continue
            node["version"] += 1
            node["status"] = "pending"
            node.pop('modelWait', None)
            node['error'] = None
            node["progress"] = 0
            node["output"] = None
            node["evidenceIds"] = []
            node["startedAt"] = None
            node["finishedAt"] = None
            node["elapsedMs"] = 0
            self._executions.pop(node_id, None)
            if self._children(node_id):
                node["phase"] = "aggregate"
            elif node["phase"] == "aggregate":
                node["phase"] = "plan" if node["kind"] == "research" else "execute"
            self._invalidations.append((node_id, cause))
            self._activity("user", "结果已失效：" + cause, node)

    def _clear_report_gate(self):
        for checkpoint in self._state["checkpoints"]:
            if (checkpoint.get("round", 1) == self._state["project"]["round"]
                    and checkpoint["type"] in ("comparison", "final") and checkpoint["status"] != "superseded"):
                checkpoint["status"] = "superseded"
                if checkpoint["id"] == self._state["activeCheckpointId"]:
                    self._state["activeCheckpointId"] = None
        self._state["report"] = self._empty_report()

    def _intervene(self, payload):
        if payload.get("expectedRevision") != self._state["revision"]:
            raise ValueError("状态版本已变化，请重新查看影响预览后提交")
        node = self._action_node(payload)
        kind = self._kind(payload.get("kind"))
        text = _text(payload.get("text"))
        if (kind == 'modify' and node['input'].get('claimId') and isinstance(payload.get('text'), str)
                and get_claim(self._state, node['input']['claimId']).get('ownerNodeId') == node['id']):
            text = payload['text']
        if not text.strip():
            raise ValueError("请填写干预原因或新的任务描述")
        from .claim_runtime import claim_intervention
        editorial_only = claim_intervention(self, node, dict(payload, text=text))
        if editorial_only:
            node['input']['description'] = text
            if self._state['report'].get('ready'):
                from .claim_runtime import report_from_claims
                report_from_claims(self._state)
            self._history('claim-editorial-edit', nodeId=node['id'], claimId=node['input'].get('claimId'), text=text, affectedIds=[])
            self._activity('user', '已保存主张排版修订；语义版本与已有证据保持有效。', node)
            return
        affected = self._affected(node["id"], kind)
        if payload.get("library") is not None:
            self._merge_library(payload["library"])
        updated_requirements = None
        if "requirements" in payload:
            if node["id"] != "central" or kind != "modify":
                raise ValueError("全局需求修改必须通过中央代理的修改操作提交")
            updated_requirements = self._validated_requirements(payload["requirements"])
        initial_gate = self._pending_checkpoint()
        keep_gate = bool(initial_gate and initial_gate["type"] in ("requirements", "recommendations"))
        was_paused = self._manual_paused
        self._clear_report_gate()
        if self._state['project'].get('researchCycle') and kind in ('insert', 'deepen'):
            from .research_cycle import insert
            origin = copy.deepcopy(node)
            self._invalidate(['central'], text, supersede_decision=True)
            insert(self, origin, dict(payload, text=text))
            self._state.update(stage=6, paused=was_paused)
            self._history('intervention', nodeId=node['id'], kind=kind, text=text, affectedIds=['central'])
            self._activity('user', '已追加研究方向：' + text, node)
            return
        if self._autonomous():
            self._state["project"]["researchIteration"] = 1 if self._state["project"].get("researchStarted") else 0
        if not node["active"]:
            parent_id = self._activation_parent(node)
            node["active"] = True
            node["parentId"] = parent_id if node["id"] != "central" else None
            if node["id"] != "central":
                self._link(parent_id, node["id"])
        self._invalidate(affected, text, supersede_decision=True)
        if kind == "modify":
            node["input"]["description"] = text
            if "acceptance" in payload:
                if not _text(payload["acceptance"]):
                    raise ValueError("验收标准不能为空")
                node["input"]["acceptance"] = _text(payload["acceptance"])
            if "experiment" in payload:
                node["input"]["experiment"] = copy.deepcopy(payload["experiment"])
        elif kind in ("insert", "deepen"):
            child = {"title": text[:80], "description": text,
                     "acceptance": _text(payload.get("acceptance")) or "提供可定位证据并明确未完成条件",
                     "constraints": _text(payload.get("constraints")),
                     "kind": "research" if kind == "deepen" else payload.get("taskKind", "research")}
            for key in ("experiment", "paperIds", "sourceNodeId", "requirementIds", "allowNewSearch", "searchBudgetId", "paperScopeExplicit", "retrieval"):
                if key in payload:
                    child[key] = copy.deepcopy(payload[key])
            self._validate_children(node, [child])
            self._add_children(node, [child])
            node["phase"] = "aggregate"
        elif kind == "reject":
            descendants = set(self._descendants(node["id"])) | {node["id"]}
            for child in self._state["nodes"]:
                if child["id"] in descendants:
                    child["active"] = child["id"] == node["id"]
                    child["status"] = "completed"
                    child["progress"] = 100
                    child["finishedAt"] = _now()
                    child["output"] = {"summary": "用户否决：" + text, "evidenceIds": [],
                                       "claims": [], "structured": {"rejected": True}, "unresolved": [text]}
            if node["id"] == "central":
                node["active"] = True
                node["status"] = "pending"
                if self._autonomous():
                    self._state["project"]["researchStarted"] = False
                    self._state["paused"] = True
                    self._state["stage"] = 0
                    node.update(phase="plan", output=None)
                else:
                    self._create_checkpoint("requirements")
                keep_gate = True
        if updated_requirements is not None:
            self._state["requirements"] = updated_requirements
            self._stop_running("研究需求已修改")
            self._history("scope-reset", nodes=copy.deepcopy(self._state["nodes"]),
                          edges=copy.deepcopy(self._state["edges"]))
            for child in self._state["nodes"]:
                child["active"] = child["id"] == "central"
            self._state["edges"] = []
            node["phase"] = "plan"
            node["requirementIds"] = [item["id"] for item in updated_requirements]
            search_settings = {key: node["input"][key] for key in ("allowNewSearch", "searchBudgetId") if key in node["input"]}
            node["input"] = {"description": "；".join(item["description"] for item in updated_requirements),
                             "acceptance": "；".join(item["acceptance"] for item in updated_requirements),
                             "constraints": "；".join(item["constraints"] for item in updated_requirements), **search_settings}
            for checkpoint in self._state["checkpoints"]:
                if (checkpoint.get("round", 1) == self._state["project"]["round"]
                        and checkpoint["status"] != "superseded"):
                    checkpoint["status"] = "superseded"
            self._state["activeCheckpointId"] = None
            if not self._autonomous():
                self._create_checkpoint("requirements")
                keep_gate = True
        if not keep_gate:
            self._state["stage"] = 6
            self._state["paused"] = was_paused or (self._autonomous() and not self._state["project"].get("researchStarted"))
        from .research_cycle import rebuild
        rebuild(self, node, kind, text, full_reset=updated_requirements is not None)
        self._history("intervention", nodeId=node["id"], kind=kind, text=text, affectedIds=affected)
        self._activity("user", "已提交研究干预：" + text, node)

    def _descendants(self, node_id):
        found = []
        queue = [node_id]
        while queue:
            parent = queue.pop()
            for child in self._children(parent):
                if child["id"] not in found:
                    found.append(child["id"])
                    queue.append(child["id"])
        return found

    def _rollback_command(self, payload):
        self._rollback(payload.get("checkpointId"))

    def _rollback(self, checkpoint_id):
        current = self._state
        checkpoint = next((item for item in current["checkpoints"] if item["id"] == checkpoint_id), None)
        if not checkpoint:
            raise ValueError("找不到指定检查点")
        restored = self._store.checkpoint(checkpoint_id)
        self._epoch += 1
        self._executions.clear()
        original = {item["id"]: item for item in restored["checkpoints"]}
        timeline = []
        for item in current["checkpoints"]:
            replacement = copy.deepcopy(original.get(item["id"], item))
            if item["id"] not in original:
                replacement["status"] = "superseded"
            if item["id"] == checkpoint_id:
                replacement["status"] = "pending"
                replacement.pop("resolvedAt", None)
                replacement.pop("decision", None)
                replacement.pop("userNote", None)
            timeline.append(replacement)
        versions = {node["id"]: node["version"] for node in current["nodes"]}
        for node in restored["nodes"]:
            node["version"] = max(node["version"], versions.get(node["id"], 0)) + 1
            if node["status"] == "running":
                node["status"] = "pending"
            self._invalidations.append((node["id"], "回滚检查点"))
        restored["revision"] = current["revision"]
        restored["activities"] = current["activities"]
        restored["history"] = current["history"]
        restored["checkpoints"] = timeline
        restored["activeCheckpointId"] = checkpoint_id
        restored["paused"] = True
        self._manual_paused = False
        restored["report"]["approved"] = False
        restored["report"]["ready"] = False
        preserve_history(restored, current)
        self._state = restored
        if self._autonomous():
            self._state["activeCheckpointId"] = None
            self._manual_paused = True
            for item in self._state["checkpoints"]:
                if item["id"] == checkpoint_id:
                    item["status"] = "confirmed"
                    item["automatic"] = True
        self._history("rollback", checkpointId=checkpoint_id, fromRevision=current["revision"])
        self._activity("user", "已回滚到研究快照：" + checkpoint["title"] +
                       ("；保持暂停，可继续研究。" if self._autonomous() else "；需要重新确认。"))

    def _feedback(self, payload):
        value = payload.get("value")
        if value not in (None, "interested", "not_interested", "read_later"):
            raise ValueError("论文标记无效")
        paper = next((paper for paper in self._state["papers"] if paper["id"] == str(payload.get("paperId"))), None)
        if paper is None:
            raise ValueError("找不到该论文")
        paper["feedback"] = value
        self._activity("user", f"已更新论文标记：{paper['title']} → {value or '清除标记'}")
        self._history("paper-feedback", paperId=paper["id"], value=value)

    def _retry(self, payload):
        from .topic_selection import pending
        node = self._get_node(payload.get("nodeId"))
        if 'expectedNodeVersion' in payload and (type(payload['expectedNodeVersion']) is not int or payload['expectedNodeVersion'] != node['version']):
            raise ValueError('节点版本已变化，请查看最新状态后重试')
        if node["status"] != "failed":
            raise ValueError("只有失败节点可以重试")
        retry_phase = (node.get('error') or {}).get('phase', node['phase'])
        if (node.get('error') or {}).get('nextRetryAt'):
            node['retryNotBefore'] = node['error']['nextRetryAt']
        self._invalidate([node["id"]], "用户重试失败任务")
        # An aggregate reviewer must reuse completed children, not re-run their experiments.
        node['phase'] = retry_phase
        if not self._pending_checkpoint() and not self._state['project'].get('researchDecision') and not pending(self._state['project']):
            self._state["paused"] = False
            self._manual_paused = False
        self._activity("user", "已安排失败任务重试。", node)

    def _mode(self, payload):
        mode = payload.get("mode")
        if mode not in ("evidence", "llm"):
            raise ValueError("执行模式无效")
        if not self._state["paused"] and self._state["status"] != "idle":
            raise ValueError("请先暂停研究，再切换执行模式")
        self._stop_running("执行模式切换")
        self._state["project"]["mode"] = mode
        self._activity("user", "已切换执行模式：" + ("本机资料核验" if mode == "evidence" else "模型研究"))

    def _next_round(self, payload):
        self._stop_running("开始下一轮")
        for claim in self._state['claimGraph']['claims']:
            claim['archived'] = True
        self._state['project']['researchDecision'] = None
        self._manual_paused = False
        self._history("round-complete", report=copy.deepcopy(self._state["report"]),
                      requirements=copy.deepcopy(self._state["requirements"]))
        self._state['project'].pop('researchCycle', None)
        for checkpoint in self._state["checkpoints"]:
            if checkpoint["status"] == "pending":
                checkpoint["status"] = "superseded"
        self._state["project"]["round"] += 1
        self._state["project"].update(researchStarted=False, researchIteration=0)
        self._state["edges"] = []
        facets = {str(item["id"]): item for item in self._state["facetNodes"]}
        retained = []
        for node in self._state["nodes"]:
            if node["id"] != "central" and not node["id"].startswith("facet:"):
                node['active'] = False
                node['input']['superseded'] = True
                retained.append(node)
                continue
            node["version"] += 1
            node.update(status="pending", progress=0, output=None, evidenceIds=[],
                        startedAt=None, finishedAt=None, elapsedMs=0, phase="plan",
                        active=node["id"] == "central")
            if node["sourceNodeId"] in facets:
                parent = facets[node["sourceNodeId"]].get("parentId")
                node["parentId"] = "facet:" + str(parent) if parent is not None else None
            retained.append(node)
        self._state["nodes"] = retained
        self._state["report"] = self._empty_report()
        if self._autonomous():
            self._state["activeCheckpointId"] = None
            self._state["stage"] = 0
            self._state["paused"] = True
            root = self._get_node("central")
            root["input"] = {key: "；".join(item[key] for item in self._state["requirements"])
                             for key in ("description", "acceptance", "constraints")}
        else:
            self._create_checkpoint("requirements")
        self._activity("user", f"已准备第 {self._state['project']['round']} 轮；" +
                       ("编辑需求后开始研究。" if self._autonomous() else "请确认本轮需求。"))

    def _refresh_library(self, payload):
        if any(node["status"] == "running" for node in self._state["nodes"]):
            raise ValueError("请先暂停活动任务，再刷新论文库")
        self._merge_library(payload.get("library"))
        self._activity("user", "已刷新本机论文库；人工标记和现有研究结果已保留。")

    def _merge_library(self, library):
        if not isinstance(library, dict):
            raise ValueError("论文库刷新数据无效")
        for key in ("papers", "evidence", "facetNodes"):
            incoming = library.get(key)
            if not isinstance(incoming, list) or any(not isinstance(item, dict) or "id" not in item for item in incoming):
                raise ValueError("论文库刷新缺少有效的 " + key)
            existing = {str(item["id"]): item for item in self._state[key]}
            for item in incoming:
                value = copy.deepcopy(item)
                value["id"] = str(value["id"])
                if key == 'evidence':
                    value.pop('researchValidation', None)
                    if value['id'] in existing and existing[value['id']].get('extractor') == 'local_process':
                        continue
                if key == "papers" and value["id"] in existing:
                    value["feedback"] = existing[value["id"]].get("feedback")
                    value["workerStatus"] = existing[value["id"]].get("workerStatus", "pending")
                existing[value["id"]] = value
            self._state[key] = list(existing.values())
        known = {node["id"] for node in self._state["nodes"]}
        for facet in self._state["facetNodes"]:
            node_id = "facet:" + str(facet["id"])
            if node_id in known:
                continue
            parent = facet.get("parentId")
            node = self._node(node_id, "facet:" + str(parent) if parent is not None else None,
                              _text(facet.get("title")) or str(facet["id"]), "research", str(facet["id"]),
                              {"description": _text(facet.get("title")), "acceptance": "提供可定位证据",
                               "constraints": "", "paperIds": copy.deepcopy(facet.get("paperIds", []))}, False)
            node["requirementIds"] = [item["id"] for item in self._state["requirements"]]
            self._state["nodes"].append(node)
        self._library = {**self._library, **copy.deepcopy(library)}

    def _link(self, parent_id, child_id):
        for source, target, kind, reason in (
            (parent_id, child_id, "decompose", "需求逐级分解"),
            (child_id, parent_id, "return", "子任务完成后向上汇总"),
        ):
            if not any(edge["source"] == source and edge["target"] == target and edge["type"] == kind
                       for edge in self._state["edges"]):
                self._state["edges"].append({"source": source, "target": target, "type": kind, "reason": reason})

    def _depth(self, node):
        depth = 0
        seen = set()
        while node["parentId"] and node["active"]:
            if node["id"] in seen:
                raise ValueError("研究任务层级存在循环")
            seen.add(node["id"])
            node = self._get_node(node["parentId"])
            depth += 1
        return depth

    def _validate_children(self, parent, children):
        if not isinstance(children, list):
            raise ValueError("子任务必须是列表")
        source_ids = {str(item["id"]) for item in self._state["facetNodes"]}
        requirement_ids = {item["id"] for item in self._state["requirements"]}
        count = sum(node["active"] for node in self._state["nodes"])
        visited = set()

        def validate(items, depth):
            nonlocal count
            if items and depth > self._limit('maxDepth'):
                raise ValueError(f"研究任务分解深度不能超过 {self._limit('maxDepth')} 层")
            if not isinstance(items, list):
                raise ValueError("嵌套子任务必须是列表")
            for child in items:
                count += 1
                if count > self._limit('maxTasks'):
                    raise ValueError(f"活动研究任务总数不能超过 {self._limit('maxTasks')}")
                if not isinstance(child, dict) or id(child) in visited:
                    raise ValueError("研究任务层级无效或含重复引用")
                visited.add(id(child))
                if any(not _text(child.get(key)) for key in ("title", "description", "acceptance")):
                    raise ValueError("每个子任务必须包含名称、需求描述和验收标准")
                if child.get("kind") not in ("research", "evidence", "experiment"):
                    raise ValueError("研究任务类型无效")
                if child.get("sourceNodeId") is not None and str(child["sourceNodeId"]) not in source_ids:
                    raise ValueError("子任务引用了不存在的切面节点")
                if "requirementIds" in child and (not isinstance(child["requirementIds"], list)
                        or set(child["requirementIds"]) - requirement_ids):
                    raise ValueError("子任务引用了未知研究需求")
                if "children" in child:
                    validate(child["children"], depth + 1)
        validate(children, self._depth(parent) + 1)
        json.dumps(children, allow_nan=False)

    def _add_children(self, parent, children):
        for child in children:
            explicit_source = str(child["sourceNodeId"]) if child.get("sourceNodeId") is not None else None
            source = explicit_source or parent["sourceNodeId"]
            reusable = next((node for node in self._state["nodes"]
                             if explicit_source and node["id"] == "facet:" + explicit_source
                             and not node["active"] and node["id"] != parent["id"]), None)
            input_data = copy.deepcopy(child)
            nested = input_data.pop("children", [])
            for key in ("allowNewSearch", "searchBudgetId", "claimId", "claimVersion"):
                if key not in input_data and key in parent["input"]:
                    input_data[key] = copy.deepcopy(parent["input"][key])
            input_data["parentDemand"] = copy.deepcopy(parent["input"])
            if reusable:
                node = reusable
                node.update(parentId=parent["id"], title=_text(child["title"]), kind=child["kind"],
                            phase="plan" if child["kind"] == "research" else "execute",
                            status="pending", active=True, progress=0, input=input_data, output=None,
                            evidenceIds=[], startedAt=None, finishedAt=None, elapsedMs=0,
                            version=node["version"] + 1)
            else:
                node = self._node(_id("task"), parent["id"], _text(child["title"]),
                                  child["kind"], source, input_data, True)
                self._state["nodes"].append(node)
            node["requirementIds"] = copy.deepcopy(child.get("requirementIds") or parent["requirementIds"]
                                                      or [item["id"] for item in self._state["requirements"]])
            self._link(parent["id"], node["id"])
            if nested:
                self._add_children(node, nested)
                node["phase"] = "aggregate"

    def _node_ready(self, node):
        if not node['active'] or node['status'] != 'pending' or node['input'].get('superseded'):
            return False
        cycle = self._state['project'].get('researchCycle')
        if (cycle and self._state['project'].get('taskMode') != 'reproduction'
                and node['input'].get('researchStep') not in ('background', 'literature', 'topic')
                and (cycle.get('topicSelection') or {}).get('status') != 'selected'):
            return False
        return (all(self._get_node(i)['status'] == 'completed' for i in node['input'].get('dependsOn', []))
                and all(child['status'] == 'completed' for child in self._children(node['id'])))

    def _next_job(self):
        from .topic_selection import pending
        if pending(self._state['project']):
            return None
        if (self._state["paused"] or self._pending_checkpoint() or self._state['project'].get('researchDecision') or self._state["report"]["approved"]
                or self._state["report"].get("ready")
                or (self._autonomous() and not self._state["project"].get("researchStarted"))):
            return None
        if len(self._executions) >= self._limit('maxParallel'):
            return None
        for node in self._state["nodes"]:
            if not self._node_ready(node):
                continue
            dependencies = node['input'].get('dependsOn', [])
            remaining_depth = max(0, self._limit('maxDepth') - self._depth(node))
            remaining_tasks = max(0, self._limit('maxTasks') - sum(n['active'] for n in self._state['nodes']))
            children = self._children(node["id"])
            if children:
                if not all(child["status"] == "completed" for child in children):
                    continue
                node["phase"] = "aggregate"
            elif node["phase"] == "aggregate":
                node["phase"] = "execute"
            elif node["phase"] == "plan" and (remaining_depth == 0 or (self._autonomous() and remaining_tasks == 0)):
                node["phase"] = "execute"
                self._activity("system", "已到达研究分解的深度或任务数量边界，直接执行当前需求；不再重复创建子任务。", node)
            if node["parentId"]:
                parent = self._get_node(node["parentId"])
                node["input"]["parentDemand"] = {key: copy.deepcopy(parent["input"].get(key, ""))
                                                 for key in ("description", "acceptance", "constraints")}
                demands = []
                ancestor = node
                while ancestor["parentId"]:
                    ancestor = self._get_node(ancestor["parentId"])
                    demands.append({"id": ancestor["id"], **{
                        key: copy.deepcopy(ancestor["input"].get(key, ""))
                        for key in ("description", "acceptance", "constraints")}})
                node["input"]["ancestorDemands"] = demands
            node["status"] = "running"
            node["progress"] = 15
            node["startedAt"] = _now()
            node["finishedAt"] = None
            node['error'] = None
            node.pop('modelWait', None)
            token = {"id": _id("run"), "epoch": self._epoch, "version": node["version"],
                     "started": time.monotonic(), "nodeId": node["id"], "phase": node["phase"]}
            self._executions[node["id"]] = token
            self._state["stage"] = 4 if node["id"] == "central" and node["phase"] == "plan" else 6
            self._activity("AI", "开始" + {"plan": "分解研究需求", "execute": "执行研究任务", "aggregate": "汇总子节点结果"}[node["phase"]], node)
            self._commit()
            library = copy.deepcopy(self._library)
            for key in ("papers", "evidence", "facetNodes"):
                library[key] = copy.deepcopy(self._state[key])
            context = {"library": library, "requirements": copy.deepcopy(self._state["requirements"]),
                       "children": copy.deepcopy(children), "mode": self._state["project"]["mode"],
                       "round": self._state["project"]["round"],
                       "workflow": self._state["project"].get("workflow", "gated"),
                       "iteration": self._state["project"].get("researchIteration", 1),
                       "maxIterations": self._limit("maxIterations"), "remainingDepth": remaining_depth,
                       "remainingTasks": remaining_tasks}
            parents = []
            ancestor = node
            while ancestor["parentId"]:
                ancestor = self._get_node(ancestor["parentId"])
                parents.append({"id": ancestor["id"], "input": copy.deepcopy(ancestor["input"])})
            context["ancestors"] = parents
            context['paperResearch'] = self._state['project'].get('paperResearch', False)
            context['paperContext'] = copy.deepcopy(self._state['project'].get('paperContext', {}))
            context['researchChoices'] = copy.deepcopy(self._state['project'].get('researchChoices', []))
            context['researchCycle'] = copy.deepcopy(self._state['project'].get('researchCycle'))
            context['modelNotBefore'] = node.get('retryNotBefore')
            context['taskMode'] = self._state['project'].get('taskMode', 'research')
            context['claimGraph'] = copy.deepcopy(self._state['claimGraph'])
            context['evidenceApprovals'] = copy.deepcopy(self._state['project'].get('researchEvidenceApprovals', {}))
            context['claim'] = copy.deepcopy(get_claim(self._state, node['input']['claimId'])) if node['input'].get('claimId') else None
            context['upstreamResults'] = [copy.deepcopy(self._get_node(i)) for i in dependencies]
            if self._state['project'].get('researchCycle'):
                self._state['project']['researchCycle']['stage'] = node['input'].get('researchStep', 'synthesis')
            token["input"] = copy.deepcopy(node["input"])
            token["context"] = {key: copy.deepcopy(context[key])
                                for key in ("requirements", "children", "mode", "round", "ancestors", "workflow", "iteration", "maxIterations", "remainingDepth", "remainingTasks", "paperResearch", "paperContext", "researchChoices", "researchCycle", "upstreamResults", "taskMode", "claim")}
            return copy.deepcopy(node), context, token
        return None

    def _current(self, token):
        if self._closed or token["epoch"] != self._epoch:
            return False
        current = self._executions.get(token["nodeId"])
        if current is None or current["id"] != token["id"]:
            return False
        node = self._get_node(token["nodeId"])
        return node["version"] == token["version"] and node["status"] == "running"

    def _record_execution(self, token, result):
        """Commit process observations before another fallible model request.

        A stale/cancelled attempt is retained in history, never published as the
        evidence of a newer node execution. Receipts themselves remain on disk.
        """
        with self._condition:
            if self._closed and not self._closing:
                return
            current = self._current(token)
            execution = {key: copy.deepcopy(result.get(key)) for key in
                         ('tool', 'status', 'returnCode', 'elapsedMs', 'artifacts', 'script', 'scriptSha256', 'scriptChanged',
                          'stdoutPath', 'stderrPath', 'nodeId', 'nodeVersion', 'round', 'createdAt')}
            evidence = copy.deepcopy(result.get('evidence', []))
            execution['receipt'] = evidence[0]['locator'] if evidence else None
            execution['evidenceIds'] = [item['id'] for item in evidence]
            valid = current and result.get('status') != 'cancelled'
            token.setdefault('toolExecutions', []).append(execution)
            self._history('tool-executed', nodeId=token['nodeId'], version=token['version'],
                          round=token['context']['round'], executionToken=token['id'],
                          phase=token['phase'], valid=valid, execution=execution)
            if valid:
                known = {item['id'] for item in self._state['evidence']}
                self._state['evidence'].extend(item for item in evidence if item['id'] not in known)
                claim_id = token.get('input', {}).get('claimId')
                if claim_id:
                    from .claims import add_relation
                    for item in evidence:
                        add_relation(self._state, claim_id, item['id'],
                            reason='本机工具执行记录：' + str(result.get('status')) + '；仍需实验设计者审查',
                            quality='limited' if result.get('status') == 'completed' else 'unusable',
                            claim_version=token['input']['claimVersion'])
            self._commit()

    def _record_artifact_read(self, token, result):
        with self._condition:
            if not self._current(token): return
            entry = {k: result[k] for k in ('path', 'sha256')}
            token.setdefault('artifactReads', []).append(entry)
            self._history('experiment-artifact-reviewed', nodeId=token['nodeId'], version=token['version'],
                          executionToken=token['id'], artifact=entry)
            self._commit()

    def _record_execution_started(self, token, execution):
        with self._condition:
            if not self._current(token):
                return
            self._history('tool-started', nodeId=token['nodeId'], version=token['version'],
                          round=token['context']['round'], executionToken=token['id'], phase=token['phase'],
                          valid=True, execution=copy.deepcopy(execution))
            self._commit()

    def _recover_tool_receipts(self, artifact_root):
        root = artifact_root.resolve()
        recorded = {(entry.get('execution') or {}).get('receipt') for entry in self._state['history']
                    if entry.get('type') == 'tool-executed'}
        receipts = list((root/'runs').glob('local-*/receipt.json'))
        receipts.extend((root/'runs').glob('job-*/attempt-*/receipt.json'))
        receipts.extend((root/'runs').glob('job-*/attempt-*/recovery-receipt.json'))
        for receipt in receipts:
            relative = receipt.relative_to(root).as_posix()
            if relative in recorded or receipt.is_symlink() or not receipt.resolve().is_relative_to(root/'runs'):
                continue
            try:
                if receipt.stat().st_size > 1024*1024: continue
                result = json.loads(receipt.read_text('utf-8'))
                if not result.get('executionToken') or not result.get('nodeId'): continue
                execution = {key: result.get(key) for key in ('tool', 'status', 'returnCode', 'elapsedMs',
                             'artifacts', 'script', 'stdoutPath', 'stderrPath', 'createdAt')}
                execution['receipt'] = relative
                self._history('tool-executed', nodeId=result['nodeId'], version=result.get('nodeVersion', 1),
                    round=result.get('round', 1), executionToken=result['executionToken'],
                    valid=False, recovered=True, execution=execution)
            except (OSError, ValueError, TypeError, AttributeError):
                continue

    def _local_activity(self, token, active):
        with self._condition:
            token['localActive'] = active
            if active:
                if self._closed: raise RuntimeError('研究引擎已关闭，未启动本机工具')
                self._local_tokens[token['id']] = token
            else:
                self._local_tokens.pop(token['id'], None)
            self._condition.notify_all()

    def _record_diagnostic(self, token, diagnostic):
        with self._condition:
            if self._closed:
                return
            token['diagnostic'] = copy.deepcopy(diagnostic)
            self._history('model-diagnostics', nodeId=token['nodeId'], version=token['version'],
                          executionToken=token['id'], **copy.deepcopy(diagnostic))
            self._commit()

    def _worker(self):
        while True:
            with self._condition:
                while not self._closed:
                    job = self._next_job()
                    if job:
                        break
                    self._condition.wait()
                if self._closed:
                    return
            node, context, token = job

            def log(message, token=token):
                with self._condition:
                    if self._current(token):
                        self._activity("AI", str(message)[:20000], self._get_node(token["nodeId"]))
                        self._commit()

            try:
                if self._runner is None:
                    raise ValueError("尚未配置研究执行器，无法执行任务；请配置后重试")
                context['cancelled'] = lambda token=token: not self._current(token)
                context['model_wait'] = lambda value, token=token: self._model_wait(token, value)
                context['executionToken'] = token['id']
                context['record_diagnostic'] = lambda result, token=token: self._record_diagnostic(token, result)
                context['record_execution'] = lambda result, token=token: self._record_execution(token, result)
                context['record_artifact_read'] = lambda result, token=token: self._record_artifact_read(token, result)
                context['record_execution_started'] = lambda result, token=token: self._record_execution_started(token, result)
                context['local_activity'] = lambda active, token=token: self._local_activity(token, active)
                output = self._runner(node, context, log)
                with self._condition:
                    if self._current(token):
                        before = copy.deepcopy(self._state)
                        old_library = self._library
                        old_executions = dict(self._executions)
                        try:
                            self._accept(token, output)
                            self._commit()
                        except Exception:
                            self._state = before
                            self._library = old_library
                            self._executions = old_executions
                            self._new_checkpoints.clear()
                            self._new_outputs.clear()
                            self._invalidations.clear()
                            raise
                        self._condition.notify_all()
            except Exception as error:
                with self._condition:
                    if self._current(token):
                        current = self._get_node(token["nodeId"])
                        current.pop('modelWait', None)
                        current.update(status="failed", finishedAt=_now(), progress=0)
                        current['error'] = {'code': type(error).__name__, 'message': str(error),
                                            'at': _now(), 'phase': token['phase'], 'retryable': True}
                        from .providers import ModelConnectionError
                        if isinstance(error, ModelConnectionError):
                            current['error'].update(error.details())
                        current["elapsedMs"] += max(0, int((time.monotonic() - token["started"]) * 1000))
                        self._executions.pop(current["id"], None)
                        self._activity("system", "任务失败：" + str(error), current, "error")
                        self._history("execution-failed", nodeId=current["id"], version=current["version"],
                                      input=token.get("input", {}), context=token.get("context", {}), error=str(error),
                                      executionToken=token['id'], executions=copy.deepcopy(token.get('toolExecutions', [])),
                                      phase=token['phase'], errorType=type(error).__name__, trace=traceback.format_exc()[-6000:],
                                      **copy.deepcopy(token.get('diagnostic', {})))
                        from .research_cycle import report_failure
                        report_failure(self, current, token, error)
                        self._commit()
                        self._condition.notify_all()

    def _model_wait(self, token, value):
        with self._condition:
            if not self._current(token):
                return
            node = self._get_node(token['nodeId'])
            if node.get('modelWait') == value:
                return
            if value is None:
                node.pop('modelWait', None)
            else:
                node['modelWait'] = copy.deepcopy(value)
            self._history('model-wait', nodeId=node['id'], version=node['version'], executionToken=token['id'], wait=copy.deepcopy(value))
            self._commit()

    def _validated_output(self, node, output):
        if not isinstance(output, dict):
            raise ValueError("执行器必须返回结构化研究结果")
        result = copy.deepcopy(output)
        if not isinstance(result.get("summary"), str) or not isinstance(result.get("structured", {}), dict):
            raise ValueError("研究结果缺少有效摘要或结构化内容")
        if not isinstance(result.get("evidenceIds", []), list) or not isinstance(result.get("claims", []), list):
            raise ValueError("研究结果的证据和结论必须是列表")
        generated = result.get("generatedEvidence", [])
        if not isinstance(generated, list):
            raise ValueError("新生成证据必须是列表")
        known = {str(item["id"]) for item in self._state["evidence"]}
        for evidence in generated:
            if (not isinstance(evidence, dict) or not str(evidence.get("id", "")).startswith("experiment:")
                    or evidence.get("type") != "experiment" or evidence.get("extractor") not in ("experiment_statistics", "local_process")
                    or not _text(evidence.get("quote")) or not _text(evidence.get("locator"))):
                raise ValueError("新生成实验凭据格式无效")
            if evidence["id"] in known:
                original = next(item for item in self._state["evidence"] if item["id"] == evidence["id"])
                if evidence != original:
                    raise ValueError("实验凭据 ID 已存在且内容不同")
            known.add(evidence["id"])
        result["evidenceIds"] = _unique(result.get("evidenceIds", []))
        from .output_protocol import validate_node_output
        validate_node_output(result, known, node)
        if set(result["evidenceIds"]) - known:
            raise ValueError("研究结果引用了未知证据 ID")
        claims = []
        for value in result.get("claims", []):
            if not isinstance(value, dict) or not _text(value.get("text")) or not isinstance(value.get("evidenceIds", []), list):
                raise ValueError("候选结论格式无效")
            claim = copy.deepcopy(value)
            claim["id"] = str(claim.get("id") or _id("claim"))
            claim["text"] = _text(claim["text"])
            claim["evidenceIds"] = _unique(claim.get("evidenceIds", []))
            if set(claim["evidenceIds"]) - known:
                raise ValueError("候选结论引用了未知证据 ID")
            claim["status"] = "candidate"
            claim["nodeId"] = str(claim.get("nodeId") or node["id"])
            if not claim["evidenceIds"]:
                limitations = _text(claim.get("limitations"))
                claim["limitations"] = (limitations + "；" if limitations else "") + "无证据：该判断尚待验证"
            claims.append(claim)
        result["claims"] = claims
        result.setdefault("structured", {})
        unresolved = result.get("unresolved", [])
        if not isinstance(unresolved, list) or any(not isinstance(item, str) for item in unresolved):
            raise ValueError("未决问题必须是字符串列表")
        result["unresolved"] = unresolved
        # Planning/previous aggregation records are replaced by the next phase;
        # omitted work must survive those replacements.
        if node['phase'] != 'plan':
            for previous in (node.get('output'), node['input'].get('planningOutput')):
                for key in ('deferredByCapacity', 'deferredFollowups'):
                    items = ((previous or {}).get('structured') or {}).get(key, [])
                    if items:
                        target = result['structured'].setdefault(key, [])
                        for item in items:
                            if item not in target: target.append(copy.deepcopy(item))
        if self._autonomous():
            available = max(0, self._limit('maxTasks') - sum(n['active'] for n in self._state['nodes']))
            deferred = []
            def bounded(items):
                nonlocal available
                if not isinstance(items, list):
                    return items
                kept = []
                for item in items:
                    if available <= 0:
                        deferred.append(copy.deepcopy(item)); continue
                    available -= 1
                    item = copy.deepcopy(item)
                    if isinstance(item, dict) and 'children' in item:
                        item['children'] = bounded(item['children'])
                    kept.append(item)
                return kept
            for key in ('children', 'followups'):
                if key in result:
                    result[key] = bounded(result[key])
            if deferred:
                result['structured'].setdefault('deferredByCapacity', []).extend(deferred)
                result['unresolved'].append(f'任务数量达到上限，{len(deferred)} 项进一步拆解已保留为未执行事项；现有节点直接完成本级工作。')
        if "children" in result:
            if node["phase"] != "plan" and result["children"]:
                raise ValueError("只有需求分解阶段可以创建子任务")
            self._validate_children(node, result["children"])
        if "followups" in result:
            if not isinstance(result["followups"], list):
                raise ValueError("后续研究任务必须是列表")
            if result["followups"]:
                if node["id"] != "central" or node["phase"] != "aggregate":
                    raise ValueError("只有中央代理汇总完成后可以提出后续研究任务")
                self._validate_children(node, result["followups"])
        json.dumps(result, allow_nan=False)
        if self._state['project'].get('researchCycle') and node['input'].get('researchStep'):
            from .research_cycle import validate_output
            validate_output(node['input']['researchStep'], node['phase'], result['structured'], known, node['input'].get('hypothesisId'), node['input'].get('topicMode', 'explore'))
            if self._state['project'].get('taskMode') == 'reproduction':
                from .claim_runtime import validate_reproduction
                validate_reproduction(result['structured'].get('hypotheses', []), self._state['evidence'])
            if any(not c['evidenceIds'] for c in result['claims']):
                raise ValueError('研究论断必须带来源；未验证的想法放在猜想或未决项中')
            if result.get('children') or result.get('followups'):
                raise ValueError('研究阶段任务由证据循环调度，不得跳过数据索求或实验复核环节')
            if (node['input']['researchStep'] == 'topic' and self._state['project'].get('paperResearch')
                    and self._state['project'].get('taskMode') == 'reproduction'
                    and not result['structured'].get('researchDecision')):
                topic = result['structured']['researchTopic']
                options = [
                    {'label': '优先严格复现原设置', 'effect': '保留论文指标、数据划分与实验设置；资源不满足时明确报告缺口。'},
                    {'label': '允许按本机资源缩小规模', 'effect': '记录对原设置的全部偏离，有限规模结果不等同原实验复现。'}]
                result['structured']['researchDecision'] = {'question': '课题「' + topic['title'] + '」先侧重哪一点？',
                    'rationale': topic['rationale'], 'options': options}
        return result

    def _accept(self, token, output):
        node = self._get_node(token["nodeId"])
        if isinstance(output, dict) and output.get("sourceLibrary") is not None:
            self._merge_library(output["sourceLibrary"])
            output = {key: value for key, value in output.items() if key != "sourceLibrary"}
        output = self._validated_output(node, output)
        followups = output.get("followups", []) if self._autonomous() and node["id"] == "central" else []
        iteration = self._state["project"].get("researchIteration", 1)
        continue_research = bool(followups and iteration < self._limit('maxIterations'))
        if self._autonomous() and node["id"] == "central" and node["phase"] != "plan":
            output["structured"]["autonomousIterations"] = iteration
            if followups and not continue_research:
                output["structured"]["deferredFollowups"] = copy.deepcopy(followups)
                output["unresolved"].append(f"本轮自动研究预算已用完（最多 {self._limit('maxIterations')} 次研究迭代）；其余后续任务尚未执行，可发起下一轮。")
        known = {item["id"] for item in self._state["evidence"]}
        self._state["evidence"].extend(copy.deepcopy(item) for item in output.get("generatedEvidence", [])
                                       if item["id"] not in known)
        node["elapsedMs"] += max(0, int((time.monotonic() - token["started"]) * 1000))
        node["output"] = output
        node["evidenceIds"] = output["evidenceIds"]
        self._new_outputs.append({"id": token["id"], "nodeId": node["id"], "version": node["version"],
                                  "phase": token["phase"], "sourceNodeId": node["sourceNodeId"],
                                  "at": _now(), "input": copy.deepcopy(token["input"]),
                                  "context": copy.deepcopy(token["context"]), "output": copy.deepcopy(output)})
        self._executions.pop(node["id"], None)
        decision = output['structured'].get('researchDecision')
        if decision and self._state['project'].get('paperResearch'):
            self._state['project']['researchDecision'] = {**copy.deepcopy(decision), 'id': _id('decision'),
                'nodeId': node['id'], 'version': node['version'], 'createdAt': _now()}
            # Mark this accepted node pending before cancelling other in-flight workers.
            node['status'] = 'pending'
            self._stop_running('等待用户研究决策')
            self._state['paused'] = True
            self._manual_paused = True
            from .research_contracts import decision_message
            self._activity('AI', decision_message(decision), self._get_node('central'))
        from .research_cycle import accept
        if accept(self, node, output, token):
            from .claim_runtime import output_relations, report_from_claims
            output_relations(self._state, node, output, token['phase'])
            if node['id'] == 'central' and node['status'] == 'completed':
                report_from_claims(self._state)
            node['evidenceIds'] = list(output['evidenceIds'])
            self._new_outputs[-1]['output'] = copy.deepcopy(output)
            return
        if node["phase"] == "plan":
            children = output.get("children", [])
            node["input"]["planningOutput"] = copy.deepcopy(output)
            node["status"] = "pending"
            node["progress"] = 30
            if children:
                self._add_children(node, children)
                node["phase"] = "aggregate"
                self._state["stage"] = 5
                self._activity("AI", f"已明确 {len(children)} 项子需求，等待子任务完成后汇总。", node)
            else:
                node["phase"] = "execute"
                self._activity("AI", "需求已明确，进入节点执行。", node)
            return
        if continue_research:
            self._add_children(node, followups)
            self._state["project"]["researchIteration"] = iteration + 1
            node.update(status="pending", phase="aggregate", progress=40, finishedAt=None)
            self._state["stage"] = 6
            self._history("autonomous-followup", iteration=iteration + 1, tasks=copy.deepcopy(followups))
            self._activity("AI", f"候选结论仍需补充核验，已追加 {len(followups)} 项任务，进入第 {iteration + 1} 次研究迭代。", node)
            return
        node.update(status="completed", progress=100, finishedAt=_now())
        self._activity("AI", "任务完成：" + output["summary"][:600], node)
        if node["id"] == "central":
            active = [item for item in self._state['nodes'] if item['active']]
            unexecuted = [{'nodeId': item['id'], 'title': item['title'],
                          'reason': (item.get('output') or {}).get('summary', '')}
                         for item in active if item['kind'] == 'experiment' and
                         ((item.get('output') or {}).get('structured') or {}).get('status') in ('missing_input', 'needs_execution')]
            unsupported = sum(not claim.get('evidenceIds') for claim in output['claims'])
            deferred = []
            for item in active:
                structured = ((item.get('output') or {}).get('structured') or {})
                for key in ('deferredByCapacity', 'deferredFollowups'):
                    if structured.get(key):
                        deferred.append({'nodeId': item['id'], 'title': item['title'],
                                         'reason': key, 'tasks': copy.deepcopy(structured[key])})
            if deferred:
                output['unresolved'].append(f"仍有 {sum(len(item['tasks']) for item in deferred)} 项计划中的研究因本轮任务或迭代上限而未执行，详见未完成任务记录。")
            output['structured']['quality'] = {'status': 'incomplete' if unexecuted or unsupported or deferred or output['unresolved'] else 'evidence_ready',
                'deferredTasks': deferred,
                'unresolvedQuestions': len(output['unresolved']),
                'unexecutedExperiments': unexecuted, 'unsupportedClaims': unsupported,
                'supportedClaims': len(output['claims'])-unsupported,
                'fullTextEvidence': sum(e.get('type') == 'full_text' for e in self._state['evidence']),
                'localExecutions': sum(e.get('tool') == 'python_run' and e.get('executionStatus') == 'completed' for e in self._state['evidence'])}
            self._state["report"] = {"summary": output["summary"], "claims": copy.deepcopy(output["claims"]),
                                     "unresolved": copy.deepcopy(output["unresolved"]),
                                     "structured": copy.deepcopy(output["structured"]),
                                     "approved": False, "ready": self._autonomous()}
            from .claim_runtime import report_from_claims
            report_from_claims(self._state)
            if self._state['project'].get('researchDecision'):
                self._state['report']['ready'] = False
                self._state['stage'] = 6
                return
            if self._autonomous():
                self._state["stage"] = 8
                self._state["paused"] = True
                self._activity("system", "本轮执行已结束，报告与原始记录已就绪。" + ('仍有未完成实验或缺证据判断，尚未完成研究验收。' if output['structured']['quality']['status']=='incomplete' else '请结合证据审阅结果。'))
            else:
                self._create_checkpoint("comparison")
