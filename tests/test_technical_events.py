from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from harnession.core import connect, save, scan
from harnession.events import parser_version, stable_id
from harnession.gateway import Gateway


class TechnicalEventsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.gateway = Gateway(self.root / 'index.sqlite3')
        self.addCleanup(self.gateway.close)
        self.roots = {name: self.root / name for name in ('codex', 'claude', 'pi', 'opencode')}
        self.roots['hermes'] = self.root / 'state.db'
        for name in ('codex', 'claude', 'pi'):
            self.roots[name].mkdir()

    def write(self, agent, records):
        path = self.roots[agent] / 'session.jsonl'
        path.write_text(''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records), encoding='utf8')
        return path

    def read(self, agent):
        scan(self.gateway.db, self.roots)
        session = self.gateway.list_sessions(agent=agent)['items'][0]
        return session, self.gateway.read_events(session['id'])['items']

    def test_codex_turn_status_deduplicates_native_pairs_and_is_searchable(self):
        abort = '<turn_aborted>\nSTATUS_NEEDLE\n</turn_aborted>'

        def message(text, role='user'):
            return dict(type='response_item', payload=dict(
                type='message', role=role, content=[dict(type='input_text', text=text)]))

        self.write('codex', [
            dict(type='session_meta', payload=dict(id='codex-status')),
            message(abort, 'developer'),
            dict(type='event_msg', payload=dict(type='turn_aborted', reason='interrupted')),
            message('第一个真实问题'),
            message('<turn_cancelled>stopped</turn_cancelled>'),
            dict(type='event_msg', payload=dict(type='turn_failed', message='failure details')),
            message('<turn_aborted>another turn</turn_aborted>'),
        ])
        session, events = self.read('codex')
        self.assertEqual([event['kind'] for event in events],
                         ['turn_status', 'message', 'turn_status', 'turn_status', 'turn_status'])
        self.assertEqual([event['metadata']['turn_status'] for event in events if event['kind'] == 'turn_status'],
                         ['interrupted', 'cancelled', 'failed', 'interrupted'])
        self.assertEqual(events[0]['text'], abort)
        self.assertIn('failure details', events[3]['text'])
        self.assertEqual((session['title'], session['excerpt']), ('第一个真实问题', '第一个真实问题'))
        self.assertEqual(self.gateway.read_events(session['id'], query='STATUS_NEEDLE')['items'][0], events[0])

    def test_claude_turn_status_upgrade_keeps_question_and_literal_examples(self):
        marker = '[Request interrupted by user for tool use]'
        example = '```xml\n<turn_aborted>示例</turn_aborted>\n```'
        self.write('claude', [
            dict(type='user', uuid='interrupt', sessionId='claude-status', message=dict(content=marker)),
            dict(type='user', uuid='question', sessionId='claude-status', message=dict(content=[
                dict(type='text', text='请解释这个例子'), dict(type='text', text=example),
                dict(type='text', text='[Request interrupted by user]'),
            ])),
        ])
        session, events = self.read('claude')
        self.assertEqual([event['kind'] for event in events], ['turn_status', 'message', 'turn_status'])
        self.assertEqual(session['excerpt'], '请解释这个例子\n' + example)
        key = self.gateway.session(session['id'])['key']
        self.gateway.db.execute('UPDATE sessions SET title=? WHERE key=?', (marker, key))
        self.gateway.db.execute('UPDATE event_state SET parser_version=5 WHERE session_key=?', (key,))
        self.gateway.db.commit()
        reopened = Gateway(self.root / 'index.sqlite3')
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get_session(session['id'])['title'], '请解释这个例子 ' + example.replace('\n', ' '))
        self.assertEqual(reopened.read_events(session['id'])['items'], events)

    def test_pi_stop_reason_adds_status_after_any_partial_reply(self):
        self.write('pi', [
            dict(type='session', id='pi-status', version=3),
            dict(type='message', id='u', message=dict(role='user', content='提问')),
            dict(type='message', id='a', message=dict(role='assistant', content='部分回答',
                                                     stopReason='aborted', errorMessage='停止原因')),
            dict(type='message', id='b', message=dict(role='assistant', content=[], stopReason='error')),
        ])
        session, events = self.read('pi')
        self.assertEqual([event['kind'] for event in events], ['message', 'message', 'turn_status', 'turn_status'])
        self.assertEqual([event['text'] for event in events], ['提问', '部分回答', '停止原因', 'error'])
        self.assertEqual([event['metadata']['turn_status'] for event in events[2:]], ['interrupted', 'failed'])
        self.assertEqual(len({event['id'] for event in events}), 4)
        self.assertEqual(session['excerpt'], '提问')

    def claude(self):
        return [
            dict(type='system', uuid='s', sessionId='claude-1', message=dict(content='SYSTEM_NEEDLE')),
            dict(type='user', uuid='u', sessionId='claude-1', message=dict(content='正常问题')),
            dict(type='assistant', uuid='a', parentUuid='u', sessionId='claude-1', message=dict(content=[
                dict(type='thinking', thinking='THINKING_NEEDLE'),
                dict(type='tool_use', id='call-1', name='bash', input={'command': 'CALL_NEEDLE'}),
                dict(type='text', text='普通回答'),
                dict(type='tool_use', id='call-2', name='read_file', input={'path': 'file.txt'}),
            ])),
            dict(type='user', uuid='result', parentUuid='a', sessionId='claude-1', message=dict(content=[
                dict(type='tool_result', tool_use_id='call-1', content='TOOL_NEEDLE', is_error=True),
                dict(type='tool_result', tool_use_id='call-2', content=[dict(type='text', text='文件内容')]),
                dict(type='text', text='继续处理'),
            ])),
        ]

    def test_mixed_blocks_keep_order_unique_ids_metadata_and_native_parent_links(self):
        path = self.write('claude', self.claude())
        before = path.read_bytes(), path.stat().st_mtime_ns
        session, events = self.read('claude')
        self.assertEqual([e['kind'] for e in events],
                         ['system', 'message', 'thinking', 'tool_call', 'message', 'tool_call',
                          'tool_output', 'tool_output', 'message'])
        self.assertEqual(len({e['id'] for e in events}), len(events))
        self.assertEqual([e['seq'] for e in events], list(range(1, 10)))
        self.assertEqual(events[4]['parent_event_id'], events[1]['id'])
        self.assertEqual(events[-1]['parent_event_id'], events[4]['id'])
        self.assertEqual(events[6]['metadata']['tool_call_id'], 'call-1')
        self.assertEqual(events[6]['metadata']['tool_name'], 'bash')
        self.assertTrue(events[6]['metadata']['is_error'])
        self.assertTrue(events[3]['source_locator'].startswith('line:3'))
        self.assertEqual(session['title'], '正常问题')
        self.assertNotIn('NEEDLE', session['excerpt'])
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))

    def test_every_split_block_is_paginated_and_searchable_without_skips(self):
        self.write('claude', self.claude())
        session, events = self.read('claude')
        ids, cursor, generation = [], 0, None
        while True:
            page = self.gateway.read_events(session['id'], cursor=cursor, limit=1, generation=generation)
            ids.extend(e['id'] for e in page['items'])
            if page['next_cursor'] is None:
                break
            cursor, generation = page['next_cursor'], page['generation']
        self.assertEqual(ids, [e['id'] for e in events])
        for needle, kind in [('SYSTEM_NEEDLE', 'system'), ('THINKING_NEEDLE', 'thinking'),
                             ('CALL_NEEDLE', 'tool_call'), ('TOOL_NEEDLE', 'tool_output')]:
            with self.subTest(needle=needle):
                self.assertEqual(len(self.gateway.list_sessions(query=needle)['items']), 1)
                hit = self.gateway.read_events(session['id'], query=needle, limit=1)
                self.assertTrue(hit['located'])
                self.assertEqual(hit['items'][0]['kind'], kind)

    def test_parser_upgrade_reprocesses_unchanged_source_preserving_ids_and_overrides(self):
        path = self.write('claude', self.claude())
        session, events = self.read('claude')
        key = 'claude:' + str(path.resolve())
        db = self.gateway.db
        rowid = db.execute('SELECT rowid FROM sessions WHERE key=?', (key,)).fetchone()[0]
        db.execute('UPDATE event_state SET parser_version=1 WHERE session_key=?', (key,))
        db.execute("UPDATE sessions SET indexed_at='old' WHERE key=?", (key,))
        db.execute('INSERT INTO overrides VALUES (?,?)', (key, 'manual-project'))
        db.commit()
        self.assertIn('旧索引', self.gateway.get_session(session['id'])['coverage'])
        updated, updated_events = self.read('claude')
        self.assertEqual(updated['id'], session['id'])
        self.assertEqual(updated['project'], 'manual-project')
        self.assertEqual([e['id'] for e in updated_events], [e['id'] for e in events])
        self.assertEqual(db.execute('SELECT rowid FROM sessions WHERE key=?', (key,)).fetchone()[0], rowid)
        self.assertEqual(db.execute('SELECT parser_version FROM event_state').fetchone()[0], parser_version('claude'))
        self.assertNotEqual(updated['indexed_at'], 'old')
        scan(self.gateway.db, self.roots)
        self.assertEqual(self.gateway.list_sessions()['items'][0]['indexed_at'], updated['indexed_at'])

    def test_opening_legacy_index_restores_technical_events_without_scanning_other_files(self):
        source = self.write('claude', self.claude()).resolve()
        source.with_name('other.jsonl').write_bytes(source.read_bytes())
        before = source.read_bytes(), source.stat().st_mtime_ns
        key = 'claude:' + str(source)
        db = self.gateway.db
        save(db, 'claude', str(source), dict(native_id='claude-1', title='原会话标题', content='旧正文'), 'old')
        db.execute('INSERT INTO overrides VALUES (?,?)', (key, 'manual-project'))
        rowid = db.execute('SELECT rowid FROM sessions WHERE key=?', (key,)).fetchone()[0]
        db.commit()
        self.assertTrue(self.gateway.read_events(stable_id(key))['legacy'])

        reopened = Gateway(self.root / 'index.sqlite3')
        self.addCleanup(reopened.close)
        session_id = stable_id(key)
        self.assertEqual(reopened.bootstrap()['total'], 1)
        session = reopened.get_session(session_id)
        self.assertTrue(session['structured'])
        self.assertEqual((session['title'], session['project']), ('原会话标题', 'manual-project'))
        events = reopened.read_events(session_id)['items']
        self.assertEqual([event['kind'] for event in events],
                         ['system', 'message', 'thinking', 'tool_call', 'message', 'tool_call',
                          'tool_output', 'tool_output', 'message'])
        self.assertEqual(reopened.list_sessions(query='TOOL_NEEDLE')['items'][0]['id'], session_id)
        self.assertEqual(reopened.db.execute('SELECT rowid FROM sessions').fetchone()[0], rowid)
        self.assertEqual(before, (source.read_bytes(), source.stat().st_mtime_ns))

    def test_opening_legacy_index_keeps_snapshot_if_source_is_missing_damaged_or_replaced(self):
        missing = self.roots['claude'].resolve() / 'missing.jsonl'
        damaged = self.roots['claude'].resolve() / 'damaged.jsonl'
        replaced = self.write('claude', [dict(type='user', sessionId='new-id', message='other session')]).resolve()
        damaged.write_text('{broken', encoding='utf8')
        for source in (missing, damaged, replaced):
            save(self.gateway.db, 'claude', str(source), dict(native_id='old-id', content='older snapshot'), 'old')

        reopened = Gateway(self.root / 'index.sqlite3')
        self.addCleanup(reopened.close)
        for source in (missing, damaged, replaced):
            with self.subTest(source=source.name):
                page = reopened.read_events(stable_id('claude:' + str(source)))
                self.assertTrue(page['legacy'])
                self.assertEqual(page['items'][0]['text'], 'older snapshot')

    def test_codex_instructions_reasoning_analysis_and_tool_results(self):
        records = [
            dict(type='session_meta', payload=dict(id='codex-1', base_instructions={'text': 'BASE_PROMPT'})),
            dict(type='turn_context', payload=dict(developer_instructions='DEVELOPER_PROMPT')),
            dict(type='turn_context', payload=dict(developer_instructions='DEVELOPER_PROMPT')),
            dict(type='response_item', payload=dict(type='message', id='u', role='user',
                                                   content=[dict(type='input_text', text='first question')])),
            dict(type='response_item', payload=dict(type='reasoning', id='r',
                                                   summary=[dict(type='summary_text', text='saved reasoning')],
                                                   encrypted_content='opaque-payload')),
            dict(type='response_item', payload=dict(type='reasoning', id='encrypted', summary=[],
                                                   encrypted_content='only-encrypted')),
            dict(type='response_item', payload=dict(type='function_call', call_id='c', name='exec_command',
                                                   arguments='{\"cmd\":\"pwd\"}')),
            dict(type='response_item', payload=dict(type='function_call_output', call_id='c',
                                                   output=[dict(type='output_text', text='/demo/workspace')])),
            dict(type='response_item', payload=dict(type='message', role='assistant', channel='analysis',
                                                   content=[dict(type='output_text', text='analysis channel')])),
            dict(type='response_item', payload=dict(type='message', role='assistant',
                                                   content=[dict(type='output_text', text='final answer')])),
        ]
        self.write('codex', records)
        session, events = self.read('codex')
        self.assertEqual([e['kind'] for e in events],
                         ['system', 'system', 'message', 'thinking', 'thinking', 'tool_call',
                          'tool_output', 'thinking', 'message'])
        self.assertEqual(events[1]['role'], 'developer')
        self.assertEqual(events[3]['text'], 'saved reasoning')
        self.assertTrue(events[4]['metadata']['unavailable'])
        self.assertNotIn('only-encrypted', json.dumps(events))
        self.assertNotIn('opaque-payload', json.dumps(events))
        self.assertEqual(events[6]['metadata']['tool_name'], 'exec_command')
        self.assertEqual(events[6]['text'], '/demo/workspace')
        self.assertEqual(session['title'], 'first question')
        self.assertNotIn('BASE_PROMPT', session['excerpt'])

    def test_pi_tool_messages_thinking_and_redacted_content(self):
        records = [
            dict(type='session', id='pi1', version=3),
            dict(type='message', id='s', message=dict(role='system', content='pi system')),
            dict(type='message', id='u', message=dict(role='user', content='pi question')),
            dict(type='message', id='a', parentId='u', message=dict(role='assistant', content=[
                dict(type='thinking', thinking='pi reasoning'),
                dict(type='thinking', thinking='', redacted=True, thinkingSignature='opaque-signature'),
                dict(type='toolCall', id='p-call', name='bash', arguments={'command': 'pwd'}),
            ])),
            dict(type='message', id='t', parentId='a', message=dict(role='toolResult',
                toolCallId='p-call', toolName='bash', isError=False, content=[dict(type='text', text='/pi')])),
        ]
        self.write('pi', records)
        _, events = self.read('pi')
        self.assertEqual([e['kind'] for e in events],
                         ['system', 'message', 'thinking', 'thinking', 'tool_call', 'tool_output'])
        self.assertEqual(events[-1]['role'], 'tool')
        self.assertEqual(events[-1]['metadata']['tool_name'], 'bash')
        self.assertEqual(events[-1]['metadata']['tool_call_id'], 'p-call')
        self.assertTrue(events[3]['metadata']['unavailable'])
        self.assertNotIn('opaque-signature', json.dumps(events))

    def test_tools_only_sessions_empty_results_and_large_outputs_are_not_lost(self):
        large = 'LONG_OUTPUT' * 30000
        self.write('claude', [
            dict(type='assistant', sessionId='tools-only', message=dict(content=[
                dict(type='tool_use', name='bash', id='c', input={'command': 'echo demo'})])),
            dict(type='user', sessionId='tools-only', message=dict(content=[
                dict(type='tool_result', tool_use_id='c', content=''),
                dict(type='tool_result', tool_use_id='c', content=large)])),
        ])
        session, events = self.read('claude')
        self.assertEqual([e['kind'] for e in events], ['tool_call', 'tool_output', 'tool_output'])
        self.assertEqual(events[-1]['text'], large)
        self.assertEqual(session['title'], 'tools-only')
        self.assertEqual(session['excerpt'], '')
        self.assertEqual(events[1]['text'], '')

    def test_tool_images_remain_attachments_without_dumping_encoded_data(self):
        image = dict(type='image', source=dict(type='base64', media_type='image/png', data='aGVsbG8='))
        self.write('claude', [dict(type='user', sessionId='s', message=dict(content=[
            dict(type='tool_result', tool_use_id='c', content=[image, dict(type='text', text='image result')])]))])
        _, events = self.read('claude')
        self.assertEqual(events[0]['text'], 'image result')
        self.assertEqual(len(events[0]['attachments']), 1)
        self.assertNotIn('aGVsbG8=', json.dumps(events))

    def test_modern_hermes_optional_columns_and_hashed_system_prompt_read_only(self):
        source = self.roots['hermes']
        db = sqlite3.connect(source)
        db.executescript('''
            CREATE TABLE sessions(id TEXT,cwd TEXT,started_at REAL,last_activity_at REAL,title TEXT,system_prompt_hash TEXT);
            CREATE TABLE system_prompts(hash TEXT,prompt TEXT);
            CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,content TEXT,
                tool_calls TEXT,tool_call_id TEXT,tool_name TEXT,reasoning_content TEXT,timestamp REAL);
        ''')
        db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)', ('h', str(self.root), 1, 2, None, 'hash'))
        db.execute('INSERT INTO system_prompts VALUES (?,?)', ('hash', 'hermes system'))
        messages = [
            (1, 'h', 'user', 'hermes question', None, None, None, None, 1),
            (2, 'h', 'assistant', 'answer', json.dumps([
                {'id': 'hc', 'function': {'name': 'terminal', 'arguments': '{\"command\":\"pwd\"}'}}]),
             None, None, 'hermes reasoning', 2),
            (3, 'h', 'tool', 'hermes output', None, 'hc', None, None, 3),
        ]
        db.executemany('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)', messages)
        db.commit()
        db.close()
        before = source.read_bytes(), source.stat().st_mtime_ns
        session, events = self.read('hermes')
        self.assertEqual([e['kind'] for e in events],
                         ['system', 'message', 'thinking', 'message', 'tool_call', 'tool_output'])
        self.assertEqual(events[0]['text'], 'hermes system')
        self.assertEqual(events[-1]['metadata']['tool_name'], 'terminal')
        self.assertEqual(session['title'], 'hermes question')
        self.assertEqual(before, (source.read_bytes(), source.stat().st_mtime_ns))

        # An older index only knows the original session, even if the DB now contains more.
        with closing(sqlite3.connect(source)) as db, db:
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)', ('unindexed', None, 3, 4, None, None))
        self.gateway.db.execute('UPDATE event_state SET parser_version=0')
        self.gateway.db.execute('DELETE FROM session_events')
        self.gateway.db.commit()
        before_upgrade = source.read_bytes(), source.stat().st_mtime_ns
        reopened = Gateway(self.root / 'index.sqlite3')
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.bootstrap()['total'], 1)
        self.assertEqual(reopened.read_events(session['id'])['items'], events)
        self.assertEqual(before_upgrade, (source.read_bytes(), source.stat().st_mtime_ns))


class EventMigrationTests(unittest.TestCase):
    def test_existing_event_table_is_extended_without_replacing_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old.sqlite3'
            db = connect(path)
            db.execute('DROP TABLE session_events')
            db.executescript('''CREATE TABLE session_events(
                id TEXT PRIMARY KEY,session_key TEXT,seq INTEGER,source_event_id TEXT,parent_event_id TEXT,
                role TEXT,timestamp TEXT,text TEXT,source_locator TEXT);
                INSERT INTO session_events VALUES ('old','s',1,'u',NULL,'user',NULL,'kept','line:1');''')
            db.commit()
            db.close()
            migrated = connect(path)
            try:
                row = migrated.execute('SELECT * FROM session_events').fetchone()
                self.assertEqual(row['id'], 'old')
                self.assertEqual(row['text'], 'kept')
                self.assertEqual(row['kind'], 'message')
                self.assertEqual(json.loads(row['metadata']), {})
            finally:
                migrated.close()


if __name__ == '__main__':
    unittest.main()
