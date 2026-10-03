"""Explicit read-only legacy history, never instantiates Engine or a job worker."""
import copy
import json
from pathlib import Path
from .store import Store
from .v2_contracts import DomainError


def read_history(root):
    root=Path(root)
    record=json.loads((root/'conversation.json').read_text(encoding='utf-8'))
    if record.get('workflowVersion','legacy')!='legacy':
        raise DomainError('unsupported_workflow','只能只读导入 legacy 历史',409)
    state=Store.read_snapshot(root/'runtime'/'swarm.sqlite')
    reports=[]
    for path in sorted((root/'reports').glob('round-*.json')):
        reports.append(json.loads(path.read_text(encoding='utf-8')))
    if not state and reports:state=reports[-1]
    return {'sourceTaskId':record['id'],'workflowVersion':'legacy','document':copy.deepcopy(record.get('document',{})), 'messages':copy.deepcopy(record.get('messages',[])),'historicalRun':state,'reports':reports,'readOnly':True,'eligibleAsAuthorization':False}
