import { invoke, Channel, isTauri } from '@tauri-apps/api/core';
import type { Agent, Run, RunEvent } from './types';

export const native = isTauri();
let requestId = 0;

export async function index<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  if (native) return invoke<T>('index_request', { method, params });
  const response = await fetch('/api/rpc', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: ++requestId, protocol_version: 1, method, params }),
  });
  if (!response.ok) throw new Error('本地索引服务不可用，请启动 python -m harnession.web');
  const payload = await response.json();
  if (payload.error) throw new Error(payload.error.message);
  return payload.result as T;
}

export const listAgents = () => native ? invoke<Agent[]>('list_agents') : Promise.resolve<Agent[]>([]);
export const listRuns = () => native ? invoke<Run[]>('list_runs') : Promise.resolve<Run[]>([]);
export const startRun = (agent: string, sessionId: string | null, cwd: string) =>
  invoke<Run>('spawn_run', { agent, sessionId, cwd });
export const stopRun = (runId: string) => invoke('stop_run', { runId });
export const closeRun = (runId: string) => invoke('close_run', { runId });
export const writePty = (runId: string, bytes: number[]) => invoke('write_pty', { runId, bytes });
export const resizePty = (runId: string, cols: number, rows: number) => invoke('resize_pty', { runId, cols, rows });
export const acknowledge = (runId: string, seq: number) => invoke('acknowledge_run', { runId, seq });
export const unsubscribe = (runId: string) => invoke('unsubscribe_run', { runId });
export async function subscribe(runId: string, afterSeq: number, onEvent: (event: RunEvent) => void) {
  const channel = new Channel<RunEvent>();
  channel.onmessage = onEvent;
  await invoke('subscribe_run', { runId, afterSeq, channel });
}
