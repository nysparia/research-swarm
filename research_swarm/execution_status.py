"""A pure view of work that can run, provider waits, and failed dependencies."""


def summarize(state, ready):
    nodes = [n for n in state['nodes'] if n['active'] and not n['input'].get('superseded')]
    failed = {n['id'] for n in nodes if n['status'] == 'failed'}
    blocked = set(failed)
    children = {}
    for n in nodes:
        children.setdefault(n['parentId'], []).append(n['id'])
    while True:
        previous = len(blocked)
        for n in nodes:
            if n['status'] == 'pending' and blocked.intersection(n['input'].get('dependsOn', []) + children.get(n['id'], [])):
                blocked.add(n['id'])
        if len(blocked) == previous:
            break
    waiting = sum(n['status'] == 'running' and bool(n.get('modelWait')) for n in nodes)
    running = sum(n['status'] == 'running' and not n.get('modelWait') for n in nodes)
    runnable = sum(ready(n) for n in nodes)
    status = ('completed' if state['report'].get('ready') or state['report'].get('approved') else
              'waiting_user' if state.get('activeCheckpointId') or state['project'].get('researchDecision') or
                  ((state['project'].get('researchCycle') or {}).get('topicSelection') or {}).get('status') == 'pending' else
              'paused' if state['paused'] else
              'partially_blocked' if failed and (running or waiting or runnable) else
              'blocked' if failed else
              'waiting_provider' if waiting and not running else 'running')
    return {'status': status, 'running': running, 'waitingProvider': waiting, 'failed': len(failed),
            'blocked': len(blocked - failed), 'ready': runnable,
            'completed': sum(n['status'] == 'completed' for n in nodes)}
