import { useEffect, useState } from 'react';
import type { ModelWait } from '../types';
import { modelWaitLabel } from './executionState';

export function ModelWaitNotice({ wait, compact = false }: { wait: ModelWait; compact?: boolean }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  return <div className="sw-model-wait" role="status"><strong>等待模型服务</strong><span>{modelWaitLabel(wait, now)}</span>{!compact && <p>{wait.reason}</p>}</div>;
}
