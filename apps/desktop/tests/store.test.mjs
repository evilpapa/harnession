import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { useWorkspace } from '../src/store.ts';

beforeEach(() => useWorkspace.setState(useWorkspace.getInitialState(), true));

// Pi exists only in research-lab; Claude spans two projects.
const sessions = [
  { id: 'codex', project: 'harnession', agent: 'codex' },
  { id: 'claude-1', project: 'harnession', agent: 'claude' },
  { id: 'claude-2', project: 'orbital-studio', agent: 'claude' },
  { id: 'pi', project: 'research-lab', agent: 'pi' },
  { id: 'unclassified', project: null, agent: 'codex' },
];
const visibleIds = () => {
  const { project, agent } = useWorkspace.getState();
  return sessions.filter((session) =>
    (project === null || (session.project ?? '') === project) &&
    (!agent || session.agent === agent)
  ).map((session) => session.id);
};

test('switching workspace → Agent shows sessions outside the previous workspace', () => {
  useWorkspace.getState().selectProject('harnession');
  assert.deepEqual(visibleIds(), ['codex', 'claude-1']);
  useWorkspace.getState().patch({ selected: 'codex' });

  useWorkspace.getState().selectAgent('pi');
  assert.deepEqual(visibleIds(), ['pi']);
  assert.equal(useWorkspace.getState().selected, null);

  useWorkspace.getState().selectAgent('claude');
  assert.deepEqual(visibleIds(), ['claude-1', 'claude-2']);
});

test('switching Agent → workspace shows every Agent in the workspace', () => {
  useWorkspace.getState().selectAgent('pi');
  useWorkspace.getState().patch({ selected: 'pi' });
  useWorkspace.getState().selectProject('harnession');
  assert.deepEqual(visibleIds(), ['codex', 'claude-1']);
  assert.equal(useWorkspace.getState().selected, null);
});

test('both all buttons restore the global list', () => {
  useWorkspace.getState().selectAgent('pi');
  useWorkspace.getState().selectProject(null);
  assert.deepEqual(visibleIds(), sessions.map((session) => session.id));

  useWorkspace.getState().selectProject('harnession');
  useWorkspace.getState().selectAgent('');
  assert.deepEqual(visibleIds(), sessions.map((session) => session.id));
});

test('unclassified workspace retains its scope and can switch to an Agent', () => {
  useWorkspace.getState().selectAgent('pi');
  useWorkspace.getState().selectProject('');
  assert.deepEqual(visibleIds(), ['unclassified']);
  useWorkspace.getState().selectAgent('pi');
  assert.deepEqual(visibleIds(), ['pi']);
});

test('an empty Agent does not retain the previously selected session', () => {
  useWorkspace.getState().patch({ selected: 'codex', mode: 'split', inspector: true });
  useWorkspace.getState().selectAgent('hermes');
  assert.deepEqual(visibleIds(), []);
  assert.equal(useWorkspace.getState().selected, null);
  assert.equal(useWorkspace.getState().mode, 'split');
  assert.equal(useWorkspace.getState().inspector, true);
});
