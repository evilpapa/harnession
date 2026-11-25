from contextlib import closing, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from harnession.__main__ import main
from harnession.gateway import Gateway


def varint(value):
    binary = bytearray()
    while value >= 128:
        binary.append((value & 127) | 128)
        value >>= 7
    binary.append(value)
    return bytes(binary)


def field(number, value):
    if isinstance(value, int):
        return varint(number * 8) + varint(value)
    return varint(number * 8 + 2) + varint(len(value)) + value


class AntigravityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.database = self.root / 'index.db'
        self.gateway = Gateway(self.database, allow_delete=True)
        self.addCleanup(self.gateway.close)
        self.desktop = self.root / 'antigravity'
        self.cli = self.root / 'antigravity-cli'
        self.native_id = str(uuid4())
        self.cwd = self.root / '中文 工作区'
        (self.cwd / '.git').mkdir(parents=True)
        self.roots = {agent: self.root / agent for agent in ('codex', 'claude', 'pi', 'hermes', 'opencode')}
        self.roots['antigravity'] = self.root

    def scan(self):
        result = self.gateway.scan_sources({agent: str(path) for agent, path in self.roots.items()})
        return next(row for row in result['results'] if row['agent'] == 'antigravity')

    def native(self, profile, native_id, messages=()):
        path = profile / 'conversations' / (native_id + '.db')
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = field(1, field(1, 1700000000))
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('''
                CREATE TABLE trajectory_meta(cascade_id);
                CREATE TABLE steps(idx INTEGER PRIMARY KEY,step_type,step_format,metadata,step_payload);
            ''')
            db.execute('INSERT INTO trajectory_meta VALUES (?)', (native_id,))
            for index, (step_type, text) in enumerate(messages):
                envelope, body = (19, 2) if step_type == 14 else (20, 1)
                payload = field(1, step_type) + field(envelope, field(body, text.encode()))
                db.execute('INSERT INTO steps VALUES (?,?,?,?,?)', (index, step_type, 0, metadata, payload))
        return path

    def summaries(self, profile):
        with closing(sqlite3.connect(profile / 'conversation_summaries.db')) as db, db:
            db.execute('CREATE TABLE conversation_summaries(conversation_id,title,workspace_uris,last_modified_time)')
            db.execute('INSERT INTO conversation_summaries VALUES (?,?,?,?)',
                       (self.native_id, '会话名称', json.dumps(['https://example.test/work', self.cwd.as_uri()]),
                        '2026-11-24 17:00:00.123456789+00:00'))

    def test_native_profiles_deduplicate_and_cli_search(self):
        path = self.native(self.cli, self.native_id, [(14, '中文问题'), (15, '回答'), (132, 'tool output')])
        older = self.native(self.desktop, self.native_id, [(14, '旧副本')])
        os.utime(older, (1, 1))
        self.native(self.cli, str(uuid4()))
        self.summaries(self.desktop)
        before = path.read_bytes(), path.stat().st_mtime_ns
        self.assertIn('更新 1', self.scan()['detail'])
        items = self.gateway.list_sessions(agent='antigravity', query='中文问题', project=str(self.cwd))['items']
        self.assertEqual(len(items), 1)
        session = self.gateway.get_session(items[0]['id'])
        self.assertEqual(session['title'], '会话名称')
        self.assertEqual(session['created'], '2023-11-14T22:13:20+00:00')
        self.assertEqual(session['updated'], '2026-11-24T17:00:00.123456+00:00')
        self.assertEqual(session['source'], str(path))
        self.assertTrue(session['source_available'])
        self.assertIsNone(session['deletion']['command'])
        events = self.gateway.read_events(session['id'])['items']
        self.assertEqual([(event['role'], event['text'], event['source_locator']) for event in events],
                         [('user', '中文问题', 'steps.idx:0'), ('assistant', '回答', 'steps.idx:1')])
        self.assertIn('未变化 1', self.scan()['detail'])
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))
        output = io.StringIO()
        with patch('harnession.__main__.defaults', return_value=self.roots), redirect_stdout(output):
            self.assertEqual(main(['--db', str(self.database), 'scan', '--antigravity', str(self.cli)]), 0)
            self.assertEqual(main(['--db', str(self.database), 'list', '--agent', 'antigravity', '--search', '回答']), 0)
        self.assertIn('中文问题', output.getvalue())
        self.assertEqual(self.gateway.list_sessions(agent='antigravity')['items'][0]['id'], session['id'])

    def test_bad_step_preserves_existing_snapshot(self):
        path = self.native(self.cli, self.native_id, [(14, '保留历史')])
        self.scan()
        session, = self.gateway.list_sessions(agent='antigravity')['items']
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('UPDATE steps SET step_payload=?', (b'\x9a\x01\x05X',))
        self.assertEqual(self.scan()['status'], 'partial')
        self.assertEqual(self.gateway.read_events(session['id'])['items'][0]['text'], '保留历史')

    def test_legacy_transcript_excludes_generated_context(self):
        logs = self.desktop / 'brain' / self.native_id / '.system_generated' / 'logs'
        logs.mkdir(parents=True)
        entries = [
            dict(type='USER_INPUT', step_index=0, content='<USER_REQUEST>\n实际问题\n</USER_REQUEST>\n<ADDITIONAL_METADATA>配置'),
            dict(type='GENERIC', step_index=1, content='tool output'),
            dict(type='PLANNER_RESPONSE', step_index=2, content='回复', thinking='thinking'),
        ]
        (logs / 'transcript_full.jsonl').write_text('\n'.join(map(json.dumps, entries)), encoding='utf8')
        conversations = self.desktop / 'conversations'
        conversations.mkdir()
        (conversations / (self.native_id + '.pb')).write_bytes(b'legacy data')
        unavailable = str(uuid4())
        (conversations / (unavailable + '.pb')).write_bytes(b'legacy data')
        result = self.scan()
        self.assertEqual(result['status'], 'partial')
        self.assertIn('更新 1', result['detail'])
        self.assertIn(unavailable, result['detail'])
        session, = self.gateway.list_sessions(agent='antigravity')['items']
        self.assertEqual(session['title'], '实际问题')
        self.assertEqual([event['text'] for event in self.gateway.read_events(session['id'])['items']],
                         ['实际问题', '回复'])
