import { useEffect, useState } from 'react';
import { api, messageOf, taskPath } from '../taskApi';
import type { ExperimentJob } from './types';
import { ErrorNote } from './ui';
export function JobLogs({ taskId, job, compact = false }: { taskId: string; job: ExperimentJob; compact?: boolean }) {
  const [stream, setStream] = useState<'stdout' | 'stderr'>('stdout'); const [text, setText] = useState(''); const [error, setError] = useState('');
  useEffect(() => {
    let live = true; let timer: number; setText(''); setError('');
    const poll = async () => { try {
      const path = taskPath(taskId, `/jobs/${encodeURIComponent(job.id)}/logs?stream=${stream}`);
      let result = await api<{ text: string; bytes: number }>(path);
      if (result.bytes > 65536) result = await api(path + `&offset=${result.bytes - 65536}`);
      if (live) { setText(result.text); setError(''); }
    } catch (e) { if (live) setError(messageOf(e)); } finally { if (live && ['running', 'queued', 'cancelling'].includes(job.status)) timer = window.setTimeout(poll, 1800); } };
    void poll(); return () => { live = false; window.clearTimeout(timer); };
  }, [taskId, job.id, job.status, job.attempts.length, stream]);
  return <div className={`sw-job-logs ${compact ? 'is-compact' : ''}`}><header><div className="sw-segments"><button className={stream === 'stdout' ? 'active' : ''} onClick={() => setStream('stdout')}>标准输出</button><button className={stream === 'stderr' ? 'active' : ''} onClick={() => setStream('stderr')}>错误输出</button></div>{!compact && <span>最近 64 KB · 原始执行日志</span>}</header>{error && <ErrorNote>{error}</ErrorNote>}<pre tabIndex={0} aria-label="实验原始日志">{text || '该输出流目前没有内容。'}</pre></div>;
}
