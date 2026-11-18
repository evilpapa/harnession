import assert from 'node:assert/strict';
import { test } from 'node:test';
import { groupMessages } from '../src/messageGroups.ts';

const event = (id, kind = 'tool_output') => ({ id: String(id), kind, text: String(id) });
const run = (count, prefix = '') => Array.from({ length: count }, (_, i) => event(`${prefix}${i}`));
const flatten = (blocks) => blocks.flatMap((block) => block.type === 'technical_group' ? block.messages : [block.message]);

test('ten technical records stay individual; eleven form one group without changing order', () => {
  assert.equal(groupMessages([]).length, 0);
  assert.ok(groupMessages(run(10)).every((block) => block.type === 'message'));
  const messages = run(11);
  const blocks = groupMessages(messages);
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].type, 'technical_group');
  assert.deepEqual(flatten(blocks), messages);
});

test('normal user and assistant messages separate independent runs and stay visible', () => {
  const user = { ...event('user', 'message'), role: 'user' };
  const assistant = { ...event('assistant', 'message'), role: 'assistant' };
  const messages = [user, ...run(11, 'a'), assistant, ...run(12, 'b'), user];
  const blocks = groupMessages(messages);
  assert.deepEqual(blocks.map((block) => block.type), ['message', 'technical_group', 'message', 'technical_group', 'message']);
  assert.deepEqual(flatten(blocks), messages);
  assert.deepEqual(blocks[2].message, assistant);
});

test('thinking, calls, results and system hints share a run while instruction tags remain separate', () => {
  const kinds = ['thinking', 'tool_call', 'tool_output', 'system'];
  const messages = Array.from({ length: 12 }, (_, i) => event(i, kinds[i % kinds.length]));
  const instruction = event('agents', 'instruction');
  const short = run(9, 'short');
  const blocks = groupMessages([...messages, instruction, ...short]);
  assert.equal(blocks[0].type, 'technical_group');
  assert.equal(blocks[0].messages.length, 12);
  assert.equal(blocks[1].message, instruction);
  assert.equal(blocks.length, 11);
  assert.deepEqual(flatten(blocks), [...messages, instruction, ...short]);
});

test('appending pages merges a continuing run and retains its group identity', () => {
  const firstPage = run(60);
  const secondPage = [...run(5, 'next'), event('answer', 'message'), ...run(11, 'end')];
  const first = groupMessages(firstPage);
  const combined = groupMessages([...firstPage, ...secondPage]);
  assert.equal(first[0].id, combined[0].id);
  assert.equal(combined[0].messages.length, 65);
  assert.equal(combined[1].message.id, 'answer');
  assert.equal(combined[2].messages.length, 11);
  assert.deepEqual(flatten(combined), [...firstPage, ...secondPage]);
});
