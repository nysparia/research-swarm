"""Bounded transport recovery shared by callers of the same provider account."""
import math
import random
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec='milliseconds')


def retry_after(value, now=None):
    if not isinstance(value, str):
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            delay = date.timestamp() - (time.time() if now is None else now)
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0., delay) if math.isfinite(delay) and delay <= 365 * 86400 else None


class ModelConnectionError(RuntimeError):
    def __init__(self, message, status_code=None, retry_after_seconds=None, *, exhausted=False, next_retry_at=None):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = True
        self.retry_after_seconds = retry_after_seconds
        self.exhausted = exhausted
        self.next_retry_at = next_retry_at

    def details(self):
        return {'statusCode': self.status_code, 'retryable': self.retryable,
                'retryAfterSeconds': self.retry_after_seconds, 'retryExhausted': self.exhausted,
                'nextRetryAt': self.next_retry_at}


class ModelRequestCancelled(RuntimeError):
    pass


class WaitBudget:
    def __init__(self, seconds=300):
        self.remaining = seconds


class ProviderRecovery:
    """Only this condition is held while inspecting admission; callbacks run outside it."""
    DELAYS = (5, 15, 45)

    def __init__(self, monotonic=time.monotonic, wall_time=time.time, jitter=random.random):
        self.clock, self.wall_time, self.jitter = monotonic, wall_time, jitter
        self.wall_offset = wall_time() - monotonic()
        self.condition = threading.Condition()
        self.states = {}

    def delay(self, retry_index):
        return self.DELAYS[min(retry_index, 2)] * (1 + .2 * self.jitter())

    def _state(self, key):
        return self.states.setdefault(key, {'generation': 0, 'until': 0., 'recovering': False,
                                            'probe': None, 'successes': 0, 'reason': '', 'statusCode': None})

    def defer(self, key, delay, error):
        with self.condition:
            state = self._state(key)
            state.update(generation=state['generation'] + 1, recovering=True, successes=0,
                         until=max(state['until'], self.clock() + delay), reason=str(error), statusCode=error.status_code)
            self.condition.notify_all()

    def acquire(self, key, not_before, budget, cancelled, notify, retry_number, reason=''):
        previous = None
        while True:
            if cancelled():
                raise ModelRequestCancelled('节点已暂停或失效，停止模型请求')
            with self.condition:
                state = self._state(key)
                due = max(not_before, state['until'])
                delay = max(0., due - self.clock())
                probing = state['recovering'] and state['probe'] is not None
                if not delay and not probing:
                    ticket = {'generation': state['generation'], 'probe': state['recovering'], 'id': object()}
                    if ticket['probe']:
                        state['probe'] = ticket['id']
                    return ticket
                retry_at = timestamp(self.wall_offset + due) if delay else None
                info = {'reason': reason or state['reason'] or '等待同一接口恢复探测',
                        'retryNumber': retry_number, 'maxRetries': 3, 'nextRetryAt': retry_at,
                        'statusCode': state['statusCode'], 'kind': 'cooldown' if delay else 'probe'}
            if budget.remaining <= 0 or delay > budget.remaining:
                raise ModelConnectionError('模型服务等待超过 5 分钟自动恢复预算；请稍后重试节点',
                                           info['statusCode'], delay or None, exhausted=True, next_retry_at=retry_at)
            if info != previous:
                notify(info)
                previous = info
            # At most half a second before checking cancellation and a renewed cooldown.
            before = self.clock()
            with self.condition:
                self.condition.wait(timeout=min(.5, delay or .5, budget.remaining))
            budget.remaining -= max(0., self.clock() - before)

    def release(self, key, ticket, success):
        with self.condition:
            state = self._state(key)
            if state['probe'] is ticket['id']:
                state['probe'] = None
                if success and ticket['generation'] == state['generation']:
                    state['successes'] += 1
                    if state['successes'] >= 2:
                        state.update(recovering=False, until=0., reason='', statusCode=None)
                elif not success and ticket['generation'] == state['generation']:
                    state['successes'] = 0
            self.condition.notify_all()
