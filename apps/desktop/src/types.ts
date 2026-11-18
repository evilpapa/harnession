export type ViewMode = 'chat' | 'tui' | 'split';
export type Project = { id: string | null; name: string; path: string | null; count: number };
export type Source = { agent: string; path: string; status: string; detail: string; scanned_at: string | null };
export type Bootstrap = {
  protocol_version: number; generation: string; total: number; home_directory?: string;
  projects: Project[]; sources: Source[]; counts: Record<string, number>;
};
export type Session = {
  id: string; agent: string; title: string; project: string | null; cwd: string | null;
  created: string | null; updated: string | null; indexed_at: string; excerpt: string; structured: boolean;
};
export type Artifact = {
  id: string; event_id: string; name: string; mime: string | null; available: boolean; reason: string | null;
};
export type SessionDetail = Session & {
  source: string; native_id: string; source_available: boolean; source_status: string; source_detail: string;
  coverage: string; branch_status: string | null; artifacts: Artifact[];
  deletion: { command: string | null; session_id: string; target: string; detail: string; reason: string | null;
    native_command: string | null; file_command: string | null; method: 'native' | 'file' | null;
    native_reason: string | null; direct_reason: string | null };
  resumption: { command: string | null; session_id: string; target: string; detail: string; reason: string | null };
};
export type MessageKind = 'message' | 'tool_call' | 'tool_output' | 'thinking' | 'system' | 'instruction';
export type Message = {
  id: string; seq: number; role: 'user' | 'assistant' | 'archive' | 'tool' | 'system' | 'developer'; text: string; timestamp: string | null;
  kind: MessageKind; metadata: { tool_name?: string | null; tool_call_id?: string | null; is_error?: boolean; unavailable?: boolean;
    instruction_category?: 'agents' | 'role' | 'context' | 'instructions' | 'skill' | 'local_command'; instruction_label?: string };
  source_event_id?: string | null; parent_event_id: string | null; source_locator: string; attachments: Artifact[];
};
export type Page<T> = {
  items: T[]; next_cursor: number | null; generation: string; reset: boolean; legacy?: boolean; located?: boolean;
};
export type Agent = { id: string; label: string; installed: boolean; resume: boolean; structured_chat: boolean };
export type Run = {
  id: string; session_id: string | null; agent: string; cwd: string; pid: number | null;
  state: 'running' | 'exited'; exit_code: number | null;
};
export type RunEvent = { run_id: string; seq: number; kind: 'output' | 'gap' | 'exit'; data: string; exit_code: number | null };
