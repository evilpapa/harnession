import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from harnession.core import scan
from harnession.codex_titles import load_codex_titles
from harnession.gateway import Gateway, response
from harnession.renaming import _rename_codex_archive


class RenameTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.index = self.root / 'index.sqlite3'
        self.gateway = Gateway(self.index)
        self.addCleanup(self.gateway.close)
        self.roots = {agent: self.root / agent for agent in ('claude', 'pi', 'codex', 'hermes', 'opencode')}

    def write_session(self, agent, filename, entries, *, final_newline=True):
        path = self.roots[agent] / filename
        path.parent.mkdir(exist_ok=True)
        text = '\n'.join(json.dumps(entry) for entry in entries)
        path.write_text(text + ('\n' if final_newline else ''), encoding='utf8')
        return path

    def rename(self, indexed, title):
        return response(self.gateway, dict(id=1, method='rename_session', params=dict(
            session_id=indexed['id'], indexed_at=indexed['indexed_at'], title=title)))

    def test_claude_updates_transcript_detail_and_search_and_survives_rebuild(self):
        native_id = '06698bf0-6bfa-4229-8d48-8d6b8b8c231a'
        path = self.write_session('claude', native_id + '.jsonl', [
            dict(type='user', sessionId=native_id, uuid='u1',
                 message=dict(role='user', content='开头提问')),
            dict(type='custom-title', sessionId=native_id, customTitle='旧的标题'),
        ])
        before = path.read_bytes()
        scan(self.gateway.db, self.roots)
        indexed = self.gateway.list_sessions()['items'][0]
        original_events = self.gateway.read_events(indexed['id'])['items']
        self.assertEqual(indexed['title'], '旧的标题')

        result = self.rename(indexed, '  新标题 🙂  ')
        self.assertIn('result', result)
        self.assertEqual(result['result']['title'], '新标题 🙂')
        self.assertEqual(path.read_bytes()[:len(before)], before)
        self.assertEqual(json.loads(path.read_bytes()[len(before):]), {
            'type': 'custom-title', 'sessionId': native_id, 'customTitle': '新标题 🙂',
        })
        detail = self.gateway.get_session(indexed['id'])
        self.assertEqual(detail['title'], '新标题 🙂')
        self.assertEqual(self.gateway.list_sessions(query='新标题')['items'][0]['excerpt'], '开头提问')
        self.assertEqual(self.gateway.list_sessions(query='旧的标题')['items'], [])
        self.assertEqual(self.gateway.read_events(indexed['id'])['items'], original_events)
        with_new_index = Gateway(self.root / 'rebuilt.sqlite3')
        self.addCleanup(with_new_index.close)
        scan(with_new_index.db, self.roots)
        self.assertEqual(with_new_index.get_session(indexed['id'])['title'], '新标题 🙂')

        renamed_bytes = path.read_bytes()
        self.assertIn('error', self.rename(indexed, '过期的编辑'))
        self.assertIn('error', self.rename(detail, '\n'))
        self.assertEqual(path.read_bytes(), renamed_bytes)

    def test_pi_keeps_the_existing_branch_and_native_title(self):
        path = self.write_session('pi', 'session.jsonl', [
            dict(type='session', version=3, id='sample-pi', cwd=str(self.root)),
            dict(type='message', id='u1', parentId=None, message=dict(role='user', content='Prompt')),
            dict(type='message', id='a1', parentId='u1', message=dict(role='assistant', content='First answer')),
            dict(type='message', id='a2', parentId='u1', message=dict(role='assistant', content='Second answer')),
        ], final_newline=False)
        scan(self.gateway.db, self.roots)
        indexed = self.gateway.list_sessions()['items'][0]
        self.assertIn('result', self.rename(indexed, 'Pi 会话新名称'))
        last = json.loads(path.read_bytes().splitlines()[-1])
        self.assertEqual((last['type'], last['parentId'], last['name']), ('session_info', 'a2', 'Pi 会话新名称'))
        self.assertNotIn(last['id'], (None, 'u1', 'a1', 'a2'))
        scan(self.gateway.db, self.roots)
        self.assertEqual(self.gateway.get_session(indexed['id'])['title'], 'Pi 会话新名称')

    def test_native_failure_or_replaced_file_leaves_the_cached_title_intact(self):
        path = self.write_session('pi', 'session.jsonl', [
            dict(type='session', version=3, id='sample-pi'),
        ])
        scan(self.gateway.db, self.roots)
        indexed = self.gateway.list_sessions()['items'][0]
        with patch('harnession.gateway.rename_native', side_effect=OSError('source failure')):
            self.assertIn('error', self.rename(indexed, 'New title'))
        self.assertEqual(self.gateway.list_sessions()['items'][0], indexed)
        replacement = b'{"type":"session","version":3,"id":"different-pi"}\n'
        path.write_bytes(replacement)
        self.assertIn('error', self.rename(indexed, 'New title'))
        self.assertEqual(path.read_bytes(), replacement)
        self.assertEqual(self.gateway.list_sessions()['items'][0], indexed)

    def test_codex_archive_updates_both_native_title_stores_for_only_its_path(self):
        home = self.roots['codex']
        archive = home / 'archived_sessions' / 'rollout.jsonl.zst'
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b'Archive bytes')
        db = sqlite3.connect(home / 'state_5.sqlite')
        self.addCleanup(db.close)
        with db:
            db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY,title TEXT,name TEXT,archived INTEGER,rollout_path TEXT)')
            db.execute('INSERT INTO threads VALUES (?,?,?,?,?)', ('archive', 'old title', 'old name', 1, str(archive)))
            db.execute('INSERT INTO threads VALUES (?,?,?,?,?)', ('other', 'another title', None, 1, '/another/file'))

        with self.assertRaisesRegex(ValueError, '位置已改变'):
            _rename_codex_archive(home, home, archive, 'other', 'Unexpected')
        self.assertFalse((home / 'session_index.jsonl').exists())
        _rename_codex_archive(home, home, archive, 'archive', 'Renamed archive')
        self.assertEqual(load_codex_titles(archive.parent), {'archive': 'Renamed archive', 'other': 'another title'})
        entry = json.loads((home / 'session_index.jsonl').read_text())
        self.assertEqual((entry['id'], entry['thread_name']), ('archive', 'Renamed archive'))
        self.assertEqual(archive.read_bytes(), b'Archive bytes')
