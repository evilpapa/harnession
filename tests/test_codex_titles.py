import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from harnession.codex_titles import load_codex_titles
from harnession.core import parse_jsonl, save, scan
from harnession.gateway import Gateway


class CodexTitleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'codex'
        self.root = self.home / 'sessions'
        self.root.mkdir(parents=True)
        self.source = self.root / 'rollout.jsonl'
        self.index = self.home / 'session_index.jsonl'
        self.dbpath = Path(self.tmp.name) / 'index.sqlite3'
        self.gateway = Gateway(self.dbpath)
        self.addCleanup(self.gateway.close)
        self.roots = {agent: self.home / agent for agent in ('claude', 'pi', 'hermes', 'opencode')}
        self.roots['codex'] = self.root
        self.write_session()

    def write_session(self, name=None):
        payload = dict(id='codex-session-id', cwd=str(self.home))
        if name is not None:
            payload['name'] = name
        rows = [
            dict(type='session_meta', payload=payload),
            dict(type='response_item', payload=dict(type='message', role='user',
                content=[dict(type='input_text', text='First prompt, not the session name')])),
            dict(type='response_item', payload=dict(type='message', role='assistant',
                content=[dict(type='output_text', text='A visible answer')])),
        ]
        self.source.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf8')

    def write_index(self, name):
        with self.index.open('a', encoding='utf8') as stream:
            stream.write(json.dumps(dict(id='codex-session-id', thread_name=name,
                                         updated_at='2026-11-16T00:00:00Z')) + '\n')

    def write_state(self, name, title='Database title', legacy=False, version=5):
        path = self.home / f'state_{version}.sqlite'
        db = sqlite3.connect(path)
        if legacy:
            db.execute('CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY,title TEXT)')
            db.execute('INSERT OR REPLACE INTO threads VALUES (?,?)', ('codex-session-id', title))
        else:
            db.execute('CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY,name TEXT,title TEXT)')
            db.execute('INSERT OR REPLACE INTO threads VALUES (?,?,?)', ('codex-session-id', name, title))
        db.commit()
        db.close()
        return path

    def current(self):
        return self.gateway.list_sessions(agent='codex')['items'][0]

    def test_native_name_wins_and_legacy_title_is_a_fallback(self):
        self.write_index('Index name')
        state = self.write_state('Actual session name')
        before = state.read_bytes(), state.stat().st_mtime_ns
        scan(self.gateway.db, self.roots)
        self.assertEqual(self.current()['title'], 'Actual session name')
        self.assertEqual(self.gateway.get_session(self.current()['id'])['title'], 'Actual session name')
        self.assertEqual(before, (state.read_bytes(), state.stat().st_mtime_ns))
        self.write_state(None)
        scan(self.gateway.db, self.roots)
        self.assertEqual(self.current()['title'], 'Index name')

    def test_rename_updates_unchanged_transcript_without_changing_ids_or_messages(self):
        scan(self.gateway.db, self.roots)
        original = self.current()
        original_events = self.gateway.read_events(original['id'])['items']
        before = self.source.read_bytes(), self.source.stat().st_mtime_ns
        key = self.gateway.db.execute('SELECT key FROM sessions').fetchone()[0]
        self.gateway.db.execute('INSERT INTO overrides VALUES (?,?)', (key, 'manual-project'))
        self.gateway.db.commit()
        self.write_index('第一次命名')
        self.write_index('最终名称')
        results = scan(self.gateway.db, self.roots)
        updated = self.current()
        self.assertEqual(updated['title'], '最终名称')
        self.assertEqual(updated['id'], original['id'])
        self.assertEqual(updated['project'], 'manual-project')
        self.assertEqual(self.gateway.read_events(original['id'])['items'], original_events)
        self.assertEqual(before, (self.source.read_bytes(), self.source.stat().st_mtime_ns))
        self.assertIn('更新 1，未变化 0', next(detail for agent, _, detail in results if agent == 'codex'))
        self.assertTrue(self.gateway.list_sessions(generation='stale')['reset'])
        self.assertEqual(len(self.gateway.list_sessions(query='最终名称')['items']), 1)
        self.assertEqual(self.gateway.list_sessions(query='第一次命名')['items'], [])

    def test_opening_existing_index_refreshes_name_without_rescanning(self):
        scan(self.gateway.db, self.roots)
        session_id = self.current()['id']
        self.write_index('Renamed while Harnession was closed')
        reopened = Gateway(self.dbpath)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get_session(session_id)['title'], 'Renamed while Harnession was closed')

    def test_clearing_index_name_restores_first_message(self):
        self.write_index('Custom name')
        scan(self.gateway.db, self.roots)
        self.write_index('')
        scan(self.gateway.db, self.roots)
        self.assertEqual(self.current()['title'], 'First prompt, not the session name')

    def test_old_state_schema_and_newest_numeric_version(self):
        self.write_state(None, title='Older version', legacy=True, version=9)
        self.write_state(None, title='Latest database title', legacy=True, version=10)
        self.assertEqual(load_codex_titles(self.root)['codex-session-id'], 'Latest database title')
        self.write_index('Named in legacy index')
        self.assertEqual(load_codex_titles(self.root)['codex-session-id'], 'Named in legacy index')

    def test_malformed_optional_metadata_keeps_valid_names_and_transcripts(self):
        self.write_index('Valid name')
        with self.index.open('a', encoding='utf8') as stream:
            stream.write('{partial\n[]\n{"id":"codex-session-id","thread_name":123}\n')
        (self.home / 'state_5.sqlite').write_bytes(b'not a database')
        results = scan(self.gateway.db, self.roots)
        self.assertEqual(self.current()['title'], 'Valid name')
        self.assertEqual(next(status for agent, status, _ in results if agent == 'codex'), 'ok')
        self.assertEqual(len(self.gateway.read_events(self.current()['id'])['items']), 2)

    def test_rollout_metadata_name_and_first_prompt_fallback(self):
        self.write_session(name='Name in rollout metadata')
        self.assertEqual(parse_jsonl(self.source, 'codex')['title'], 'Name in rollout metadata')
        old_item = parse_jsonl(self.source, 'codex')
        old_item['title'] = 'Old cached first prompt'
        stat = self.source.stat()
        save(self.gateway.db, 'codex', str(self.source), old_item, f'{stat.st_mtime_ns}:{stat.st_size}')
        scan(self.gateway.db, self.roots)
        self.assertEqual(self.current()['title'], 'Name in rollout metadata')

    def test_custom_source_root_and_duplicate_id_in_another_root(self):
        self.write_index('Name for the original root')
        scan(self.gateway.db, self.roots)
        other = self.home / 'other'
        other.mkdir()
        (other / 'copy.jsonl').write_bytes(self.source.read_bytes())
        (other / 'session_index.jsonl').write_text(json.dumps(dict(
            id='codex-session-id', thread_name='Name for imported copy')) + '\n', encoding='utf8')
        roots = dict(self.roots, codex=other)
        results = scan(self.gateway.db, roots)
        titles = {item['title'] for item in self.gateway.list_sessions(agent='codex')['items']}
        self.assertEqual(titles, {'Name for the original root', 'Name for imported copy'})
        self.assertEqual(next(status for agent, status, _ in results if agent == 'codex'), 'ok')


if __name__ == '__main__':
    unittest.main()
