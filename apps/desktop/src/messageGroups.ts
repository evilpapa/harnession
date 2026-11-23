import type { Message } from './types';

export type MessageBlock =
  | { type: 'message'; message: Message }
  | { type: 'technical_group'; id: string; messages: Message[] };

const groupableKinds = new Set(['thinking', 'tool_call', 'tool_output', 'system']);

/** Collapse long consecutive runs, keeping conversations, instructions and turn status visible. */
export function groupMessages(messages: Message[]): MessageBlock[] {
  const blocks: MessageBlock[] = [];
  let pending: Message[] = [];
  const flush = () => {
    if (pending.length > 10) {
      blocks.push({ type: 'technical_group', id: pending[0].id, messages: pending });
    } else {
      for (const message of pending) blocks.push({ type: 'message', message });
    }
    pending = [];
  };
  for (const message of messages) {
    if (groupableKinds.has(message.kind)) {
      pending.push(message);
    } else {
      flush();
      blocks.push({ type: 'message', message });
    }
  }
  flush();
  return blocks;
}
