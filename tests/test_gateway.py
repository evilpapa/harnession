import base64
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from harnession.core import connect, parse_jsonl, save, scan
from harnession.events import parser_version, stable_id
from harnession.gateway import Gateway, response


# A valid 1×1 PNG, no dependency on an image service or the user's files.
PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWV8AAAAASUVORK5CYII='


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.gateway = Gateway(self.root / 'index.sqlite3')
        self.addCleanup(self.gateway.close)
        self.roots = {agent: self.root / agent for agent in ('codex', 'claude', 'pi', 'hermes', 'opencode')}
        self.roots['claude'].mkdir()
        self.cwd = self.root / '中文项目'
        self.cwd.mkdir()
        self.source = self.roots['claude'] / 'session.jsonl'

    def write(self, records, source=None):
        (source or self.source).write_text(''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records), encoding='utf8')

    def conversation(self, text='hello', image=False):
        content = [dict(type='text', text=text)]
        if image:
            content.append(dict(type='image', source=dict(type='base64', media_type='image/png', data=PNG)))
        return [dict(type='user', uuid='u1', sessionId='s1', cwd=str(self.cwd), message=dict(content=content)),
                dict(type='assistant', uuid='a1', parentUuid='u1', sessionId='s1', message=dict(content=[
                    dict(type='text', text='answer')]))]

    def scan(self):
        return scan(self.gateway.db, self.roots)

    def session(self):
        return self.gateway.list_sessions()['items'][0]

    def test_message_boundaries_are_from_records_not_role_prefixes(self):
        text = 'Example:\nuser: this is still my message\n\nassistant: not a new turn'
        self.write(self.conversation(text))
        before = self.source.read_bytes(), self.source.stat().st_mtime_ns
        self.scan()
        session = self.session()
        events = self.gateway.read_events(session['id'])['items']
        self.assertEqual([event['role'] for event in events], ['user', 'assistant'])
        self.assertEqual(events[0]['text'], text)
        self.assertEqual(events[0]['source_locator'], 'line:1')
        self.assertEqual(events[1]['parent_event_id'], events[0]['id'])
        self.assertNotIn('private', json.dumps(events))
        self.assertEqual(before, (self.source.read_bytes(), self.source.stat().st_mtime_ns))

    def test_session_and_message_ids_survive_rescan_and_manual_project(self):
        self.write(self.conversation())
        self.scan()
        session = self.session()
        original_ids = [event['id'] for event in self.gateway.read_events(session['id'])['items']]
        key = self.gateway.db.execute('SELECT key FROM sessions').fetchone()[0]
        self.gateway.db.execute('INSERT INTO overrides VALUES (?,?)', (key, 'manual'))
        self.gateway.db.commit()
        self.write(self.conversation('changed'))
        self.scan()
        self.assertEqual(self.session()['id'], session['id'])
        self.assertEqual(self.session()['project'], 'manual')
        self.assertEqual([event['id'] for event in self.gateway.read_events(session['id'])['items']], original_ids)

    def test_malformed_source_preserves_message_snapshot_and_reports_coverage(self):
        self.write(self.conversation())
        self.scan()
        session = self.session()
        original = self.gateway.read_events(session['id'])
        self.source.write_text('{bad json\n', encoding='utf8')
        self.scan()
        self.assertEqual(self.gateway.read_events(session['id']), original)
        self.assertEqual(self.gateway.get_session(session['id'])['source_status'], 'partial')

    def test_old_text_is_not_split_and_unchanged_file_is_upgraded(self):
        self.write(self.conversation('user: prefix inside text'))
        item = parse_jsonl(self.source, 'claude')
        item.pop('events')
        stat = self.source.stat()
        save(self.gateway.db, 'claude', str(self.source.resolve()), item, f'{stat.st_mtime_ns}:{stat.st_size}')
        session = self.session()
        page = self.gateway.read_events(session['id'])
        self.assertTrue(page['legacy'])
        self.assertEqual(page['items'][0]['role'], 'archive')
        self.scan()
        upgraded = self.gateway.read_events(session['id'])
        self.assertFalse(upgraded['legacy'])
        self.assertEqual(len(upgraded['items']), 2)
        self.assertEqual(self.gateway.db.execute('SELECT parser_version FROM event_state').fetchone()[0], parser_version('claude'))

    def test_paginated_events_and_search_location(self):
        self.write(self.conversation())
        self.scan()
        session = self.session()
        first = self.gateway.read_events(session['id'], limit=1)
        second = self.gateway.read_events(session['id'], cursor=first['next_cursor'], limit=1, generation=first['generation'])
        self.assertEqual(first['items'][0]['role'], 'user')
        self.assertEqual(second['items'][0]['role'], 'assistant')
        self.assertIsNone(second['next_cursor'])
        located = self.gateway.read_events(session['id'], query='answer')
        self.assertTrue(located['located'])
        self.assertEqual(located['items'][0]['role'], 'assistant')
        self.write(self.conversation('updated'))
        self.scan()
        self.assertTrue(self.gateway.read_events(session['id'], generation=first['generation'])['reset'])

    def test_list_pagination_filters_and_generation(self):
        for index in range(3):
            self.write(self.conversation('needle' if index == 1 else 'other'), self.source.with_name(f'{index}.jsonl'))
        self.scan()
        first = self.gateway.list_sessions(limit=1)
        second = self.gateway.list_sessions(limit=1, cursor=first['next_cursor'], generation=first['generation'])
        self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])
        result = self.gateway.list_sessions(agent='claude', project=str(self.cwd), query='needle')
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(self.gateway.list_sessions(agent='pi')['items'], [])
        self.assertEqual(self.gateway.list_sessions(project='wrong')['items'], [])
        self.assertTrue(self.gateway.list_sessions(generation='stale')['reset'])

    def test_explicit_image_is_read_by_id_without_dumping_image_data_into_events(self):
        self.write(self.conversation(image=True))
        self.scan()
        session = self.gateway.get_session(self.session()['id'])
        self.assertEqual(len(session['artifacts']), 1)
        image = session['artifacts'][0]
        self.assertTrue(image['available'])
        result = self.gateway.read_artifact(image['id'])
        self.assertEqual(result['data_url'], 'data:image/png;base64,' + PNG)
        with self.assertRaises(ValueError):
            self.gateway.read_artifact('/etc/passwd')
        self.assertNotIn(PNG, json.dumps(self.gateway.read_events(session['id'])))

    def test_session_deletion_recipe_uses_native_id_and_actual_scanned_source(self):
        self.write(self.conversation())
        self.scan()
        before = self.source.read_bytes(), self.source.stat().st_mtime_ns
        session = self.gateway.get_session(self.session()['id'])
        recipe = session['deletion']
        self.assertEqual(recipe['session_id'], 's1')
        self.assertNotEqual(recipe['session_id'], session['id'])
        self.assertEqual(recipe['target'], str(self.source))
        self.assertIsNotNone(recipe['command'])
        self.assertEqual(before, (self.source.read_bytes(), self.source.stat().st_mtime_ns))
        self.assertEqual(self.gateway.bootstrap()['total'], 1)
        self.assertIn('error', response(self.gateway, dict(method='delete_session')))

    def request_delete(self, session, gateway=None, *, method='delete_session'):
        return response(gateway or self.gateway, dict(method=method, params={
            'session_id': session['id'], 'indexed_at': session['indexed_at'],
        }))

    def test_direct_delete_removes_only_selected_file_and_its_cache(self):
        # Same native ID in two imported files: only the indexed source path identifies the target.
        self.source = self.source.with_name("中文 '$(example);.jsonl")
        other = self.source.with_name('other.jsonl')
        self.write(self.conversation('delete this', image=True))
        self.write(self.conversation('keep this'), other)
        keep_bytes = other.read_bytes()
        self.scan()
        session_id = self.gateway.list_sessions(query='delete this')['items'][0]['id']
        session = self.gateway.get_session(session_id)
        artifact_id = session['artifacts'][0]['id']
        key = self.gateway.session(session_id)['key']
        self.gateway.db.execute('INSERT INTO overrides VALUES (?,?)', (key, 'manual'))
        self.gateway.db.commit()

        self.assertIn('error', self.request_delete(session))
        self.assertTrue(self.source.is_file())
        desktop = Gateway(self.root / 'index.sqlite3', allow_delete=True)
        self.addCleanup(desktop.close)
        self.assertIsNone(desktop.get_session(session_id)['deletion']['direct_reason'])
        result = self.request_delete(session, desktop)
        self.assertEqual(result.get('result'), {'deleted': True}, result)

        self.assertFalse(self.source.exists())
        self.assertEqual(other.read_bytes(), keep_bytes)
        for table, column in (('sessions', 'key'), ('overrides', 'key'), ('event_state', 'session_key'),
                              ('session_events', 'session_key'), ('artifacts', 'session_key')):
            self.assertIsNone(self.gateway.db.execute(f'SELECT 1 FROM {table} WHERE {column}=?', (key,)).fetchone())
        with self.assertRaises(ValueError):
            desktop.read_artifact(artifact_id)
        self.scan()
        self.assertEqual(desktop.bootstrap()['counts'], {'claude': 1})
        self.assertEqual(self.session()['title'], 'keep this')

    def test_direct_delete_retains_snapshot_on_stale_input_or_rm_error(self):
        self.write(self.conversation(image=True))
        self.scan()
        session = self.session()
        desktop = Gateway(self.root / 'index.sqlite3', allow_delete=True)
        self.addCleanup(desktop.close)
        detail = desktop.get_session(session['id'])
        with patch('harnession.gateway.remove_session_file', side_effect=PermissionError('test refused')):
            result = self.request_delete(session, desktop)
        self.assertIn('test refused', result['error']['message'])
        self.assertEqual(desktop.get_session(session['id']), detail)
        self.assertTrue(self.source.is_file())

        result = self.request_delete(dict(session, indexed_at='stale'), desktop)
        self.assertIn('索引已更新', result['error']['message'])
        self.write(self.conversation('source changed'))
        result = self.request_delete(session, desktop)
        self.assertIn('文件已更新', result['error']['message'])
        self.assertEqual(desktop.get_session(session['id']), detail)
        self.assertIn('source changed', self.source.read_text())

    def test_delete_index_for_missing_file_cleans_snapshot_without_touching_sources(self):
        self.write(self.conversation('indexed only', image=True))
        other = self.source.with_name('other.jsonl')
        self.write(self.conversation('keep this'), other)
        before = other.read_bytes()
        self.scan()
        session = self.gateway.list_sessions(query='indexed only')['items'][0]
        key = self.gateway.session(session['id'])['key']
        self.gateway.db.execute('INSERT INTO overrides VALUES (?,?)', (key, 'manual'))
        self.gateway.db.commit()
        moved = self.source.rename(self.source.with_suffix('.saved'))
        desktop = Gateway(self.root / 'index.sqlite3', allow_delete=True)
        self.addCleanup(desktop.close)
        detail = desktop.get_session(session['id'])
        self.assertTrue(detail['source_missing'])
        self.assertFalse(detail['source_available'])
        self.assertIsNone(detail['deletion']['command'])
        self.assertEqual(len(desktop.read_events(session['id'])['items']), 2)
        self.assertIn('error', self.request_delete(session, method='delete_session_index'))
        self.assertIn('索引已更新', self.request_delete(dict(session, indexed_at='stale'), desktop,
                                                   method='delete_session_index')['error']['message'])

        with patch('harnession.gateway.remove_session_file') as remove_source:
            result = self.request_delete(session, desktop, method='delete_session_index')
            remove_source.assert_not_called()
        self.assertEqual(result.get('result'), {'deleted': True}, result)
        for table, column in (('sessions', 'key'), ('overrides', 'key'), ('event_state', 'session_key'),
                              ('session_events', 'session_key'), ('artifacts', 'session_key')):
            self.assertIsNone(desktop.db.execute(f'SELECT 1 FROM {table} WHERE {column}=?', (key,)).fetchone())
        self.assertEqual(other.read_bytes(), before)
        self.assertIn('indexed only', moved.read_text())
        self.scan()
        self.assertEqual(desktop.bootstrap()['counts'], {'claude': 1})
        self.assertEqual(self.session()['title'], 'keep this')

    def test_delete_index_rechecks_restored_source_and_refuses_unavailable_path(self):
        self.write(self.conversation())
        self.scan()
        session = self.session()
        desktop = Gateway(self.root / 'index.sqlite3', allow_delete=True)
        self.addCleanup(desktop.close)
        moved = self.source.rename(self.source.with_suffix('.saved'))
        self.assertTrue(desktop.get_session(session['id'])['source_missing'])
        moved.rename(self.source)
        restored = self.request_delete(session, desktop, method='delete_session_index')
        self.assertIn('仍存在', restored['error']['message'])

        moved = self.source.rename(self.source.with_suffix('.saved'))
        self.source.mkdir()
        self.assertFalse(desktop.get_session(session['id'])['source_missing'])
        unavailable = self.request_delete(session, desktop, method='delete_session_index')
        self.assertIn('无法确认', unavailable['error']['message'])
        self.assertEqual(desktop.bootstrap()['total'], 1)
        self.assertTrue(moved.is_file())

    def test_delete_hermes_index_checks_exact_row_in_shared_database(self):
        path = self.root / 'state.db'
        self.roots['hermes'] = path
        with sqlite3.connect(path) as source:
            source.executescript('''
                CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT, started_at REAL, last_activity_at REAL, title TEXT);
                CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT);
                INSERT INTO sessions VALUES ('one', NULL, 1, 1, 'to delete'), ('one-more', NULL, 1, 1, 'keep');
                INSERT INTO messages VALUES (1, 'one', 'user', 'cached');
            ''')
        self.addCleanup(source.close)
        self.scan()
        session = self.gateway.list_sessions(query='to delete')['items'][0]
        desktop = Gateway(self.root / 'index.sqlite3', allow_delete=True)
        self.addCleanup(desktop.close)
        self.assertFalse(desktop.get_session(session['id'])['source_missing'])
        with source:
            source.execute("DELETE FROM sessions WHERE id='one'")
        before = path.read_bytes()
        detail = desktop.get_session(session['id'])
        self.assertTrue(detail['source_missing'])
        self.assertIsNone(detail['resumption']['command'])
        result = self.request_delete(session, desktop, method='delete_session_index')
        self.assertEqual(result.get('result'), {'deleted': True}, result)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(desktop.list_sessions(agent='hermes')['items'][0]['title'], 'keep')

    def test_session_resume_recipe_uses_native_id_and_configured_profile(self):
        native_id = '019a8836-b93f-7a51-9be7-791ed467b370'
        root = self.root / 'claude profile' / 'projects'
        root.mkdir(parents=True)
        self.roots['claude'] = root
        path = root / (native_id + '.jsonl')
        records = self.conversation()
        for record in records:
            record['sessionId'] = native_id
        self.write(records, source=path)
        self.scan()
        before = path.read_bytes(), path.stat().st_mtime_ns
        session = self.gateway.get_session(self.session()['id'])
        recipe = session['resumption']
        self.assertEqual(recipe['session_id'], native_id)
        self.assertEqual(recipe['target'], str(path))
        self.assertIn('CLAUDE_CONFIG_DIR=', recipe['command'])
        self.assertIn(str(root.parent), recipe['command'])
        self.assertIn('claude --resume ' + native_id, recipe['command'])
        self.assertNotIn(session['id'], recipe['command'])
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))
        self.assertIn('error', response(self.gateway, dict(method='resume_session')))

    def test_image_path_escape_and_symlink_are_rejected(self):
        outside = self.root / 'outside.png'
        outside.write_bytes(base64.b64decode(PNG))
        link = self.cwd / 'link.png'
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('symlink not supported')
        records = self.conversation()
        records[0]['message']['content'].append(dict(type='input_image', image_url=link.as_uri()))
        self.write(records)
        self.scan()
        image = self.gateway.get_session(self.session()['id'])['artifacts'][0]
        self.assertFalse(image['available'])
        self.assertIn('工作目录', image['reason'])

    def test_image_content_signature_is_verified(self):
        records = self.conversation(image=True)
        records[0]['message']['content'][1]['source']['data'] = base64.b64encode(b'<script>bad</script>').decode()
        self.write(records)
        self.scan()
        image = self.gateway.get_session(self.session()['id'])['artifacts'][0]
        with self.assertRaises(ValueError):
            self.gateway.read_artifact(image['id'])

    def test_pi_parent_relationship_does_not_claim_active_branch(self):
        self.roots['pi'].mkdir()
        self.write([dict(type='session', id='pi1', version=3, cwd=str(self.cwd)),
            dict(type='message', id='u', parentId=None, message=dict(role='user', content='root')),
            dict(type='message', id='a', parentId='u', message=dict(role='assistant', content='branch A')),
            dict(type='message', id='b', parentId='u', message=dict(role='assistant', content='branch B'))], self.roots['pi'] / 'pi.jsonl')
        self.scan()
        session = self.gateway.list_sessions(agent='pi')['items'][0]
        events = self.gateway.read_events(session['id'])['items']
        self.assertEqual(events[1]['parent_event_id'], events[2]['parent_event_id'])
        self.assertIn('活动路径未知', self.gateway.get_session(session['id'])['branch_status'])

    def test_protocol_validation_and_error_envelopes(self):
        for request in [[], None, dict(protocol_version=9), dict(method='run_shell'),
                        dict(method='list_sessions', params=dict(limit=0)),
                        dict(method='list_sessions', params=dict(cursor=-1)),
                        dict(method='get_session', params=dict(session_id='missing'))]:
            self.assertIn('error', response(self.gateway, request))
        result = response(self.gateway, dict(id=3, method='bootstrap'))
        self.assertEqual(result['id'], 3)
        self.assertEqual(result['protocol_version'], 1)
        self.assertIn('projects', result['result'])

    def test_configured_source_paths_survive_gateway_reopen(self):
        paths = {agent: str(path) for agent, path in self.roots.items()}
        self.gateway.scan_sources(paths)
        other = Gateway(self.root / 'index.sqlite3')
        try:
            self.assertEqual(other.roots()['claude'], self.roots['claude'])
        finally:
            other.close()


class ExistingDatabaseTests(unittest.TestCase):
    def test_additive_migration_retains_cli_rowids_and_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old.sqlite3'
            db = sqlite3.connect(path)
            db.executescript('''CREATE TABLE sessions (key TEXT PRIMARY KEY,agent TEXT,source TEXT,
                native_id TEXT,title TEXT,cwd TEXT,project TEXT,created TEXT,updated TEXT,content TEXT,
                fingerprint TEXT,indexed_at TEXT); CREATE TABLE overrides (key TEXT PRIMARY KEY,project TEXT);''')
            db.execute('INSERT INTO sessions(rowid,key,agent,content,indexed_at) VALUES (42,?,?,?,?)', ('old-key', 'claude', 'user: untouched', '2026-01-01'))
            db.execute('INSERT INTO overrides VALUES (?,?)', ('old-key', 'manual'))
            db.commit(); db.close()
            migrated = connect(path)
            try:
                self.assertEqual(migrated.execute('SELECT rowid FROM sessions').fetchone()[0], 42)
                self.assertEqual(migrated.execute('SELECT project FROM overrides').fetchone()[0], 'manual')
                self.assertEqual(migrated.execute('SELECT session_id FROM event_state').fetchone()[0], stable_id('old-key'))
            finally:
                migrated.close()


if __name__ == '__main__':
    unittest.main()
