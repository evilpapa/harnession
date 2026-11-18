from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harnession.__main__ import main
from harnession.core import connect, defaults, parse_jsonl, scan


class PiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dbpath = self.root / 'index.db'
        self.db = connect(self.dbpath)
        self.addCleanup(self.db.close)
        self.roots = {name: self.root / name for name in ('codex', 'claude', 'hermes', 'pi', 'opencode')}
        self.project = self.root / '中文项目'
        (self.project / '.git').mkdir(parents=True)
        self.file = self.roots['pi'] / '--encoded-project--' / 'session.jsonl'
        self.file.parent.mkdir(parents=True)
        self.header = dict(type='session', version=3, id='pi-session-id',
                           cwd=str(self.project), timestamp='2026-01-01T00:00:00Z')

    def write(self, entries, path=None):
        (path or self.file).write_text(
            ''.join(json.dumps(entry, ensure_ascii=False) + '\n' for entry in entries),
            encoding='utf8')

    def message(self, role, content, **fields):
        return dict(type='message', message=dict(role=role, content=content), **fields)

    def row(self):
        return self.db.execute("SELECT rowid, * FROM sessions WHERE agent='pi'").fetchone()

    def pi_status(self):
        return next((status, detail) for agent, status, detail in scan(self.db, self.roots) if agent == 'pi')

    def cli(self, *args):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = main(['--db', str(self.dbpath), *args])
        return code, output.getvalue()

    def test_scan_metadata_text_and_read_only(self):
        self.write([
            self.header,
            self.message('system', 'private system prompt'),
            self.message('user', [
                dict(type='text', text='查找中文 needle'),
                dict(type='image', data='private image'),
            ], id='u1', parentId=None, timestamp='2026-01-01T00:01:00Z'),
            self.message('assistant', [
                dict(type='thinking', thinking='private reasoning'),
                dict(type='toolCall', name='bash', arguments=dict(command='private command')),
                dict(type='text', text='已找到'),
            ], id='a1', parentId='u1', timestamp='2026-01-01T00:02:00Z'),
            self.message('toolResult', [dict(type='text', text='private tool output')]),
            self.message('custom', 'private custom message'),
            dict(type='custom_message', content='private extension', display=True),
            dict(type='custom', data=dict(text='private extension state')),
            dict(type='session_info', name='  中文会话\n名称  ', timestamp='2026-01-01T00:03:00Z'),
        ])
        original = self.file.read_bytes(), self.file.stat().st_mtime_ns
        status, detail = self.pi_status()
        self.assertEqual(status, 'ok')
        self.assertIn('更新 1', detail)
        row = self.row()
        self.assertEqual(row['native_id'], 'pi-session-id')
        self.assertEqual(row['cwd'], str(self.project))
        self.assertEqual(row['project'], str(self.project))
        self.assertEqual(row['source'], str(self.file.resolve()))
        self.assertEqual(row['created'], self.header['timestamp'])
        self.assertEqual(row['updated'], '2026-01-01T00:03:00Z')
        self.assertEqual(row['title'], '中文会话 名称')
        self.assertIn('user: 查找中文 needle', row['content'])
        self.assertIn('assistant: 已找到', row['content'])
        self.assertIn('system: private system prompt', row['content'])
        self.assertIn('thinking: private reasoning', row['content'])
        self.assertIn('tool_output: private tool output', row['content'])
        self.assertNotIn('private custom', row['content'])
        self.assertNotIn('private extension', row['content'])
        kinds = [event['kind'] for event in parse_jsonl(self.file, 'pi')['events']]
        self.assertEqual(kinds, ['system', 'message', 'thinking', 'tool_call', 'message', 'tool_output'])
        self.assertEqual(original, (self.file.read_bytes(), self.file.stat().st_mtime_ns))

    def test_versions_and_legacy_entries_without_tree_ids(self):
        for version in (None, 1, 2, 3):
            with self.subTest(version=version):
                header = dict(self.header)
                if version is None:
                    header.pop('version')
                else:
                    header['version'] = version
                self.write([header, self.message('user', 'legacy'), self.message('assistant', 'reply')])
                item = parse_jsonl(self.file, 'pi')
                self.assertEqual(item['content'], 'user: legacy\n\nassistant: reply')
                self.assertEqual(item['title'], 'legacy')

    def test_latest_name_and_explicit_clears(self):
        for last_name, expected in (
            ('  新名称  ', '新名称'), ('', 'first question'),
            ('  ', 'first question'), (None, 'first question'),
        ):
            with self.subTest(last_name=last_name):
                self.write([
                    self.header, self.message('user', 'first question'),
                    dict(type='session_info', name='old name'),
                    dict(type='session_info', name=last_name),
                ])
                self.assertEqual(parse_jsonl(self.file, 'pi')['title'], expected)

    def test_empty_session_and_missing_legacy_cwd(self):
        header = dict(self.header)
        header.pop('cwd')
        self.write([header])
        self.assertEqual(self.pi_status()[0], 'ok')
        self.assertEqual(self.row()['title'], header['id'])
        self.assertIsNone(self.row()['project'])
        self.assertEqual(self.row()['content'], '')

    def test_all_branches_and_pre_compaction_history(self):
        self.write([
            self.header,
            self.message('user', 'root question', id='u1', parentId=None),
            self.message('assistant', 'branch A', id='a1', parentId='u1'),
            dict(type='compaction', summary='private summary', firstKeptEntryId='a1'),
            dict(type='branch_summary', summary='private branch summary', fromId='a1', parentId='u1'),
            self.message('assistant', 'branch B', id='a2', parentId='u1'),
            dict(type='context_edit', targetId='a1', replacement=None),
            dict(type='future_event', content='private future event'),
        ])
        self.assertEqual(parse_jsonl(self.file, 'pi')['content'],
                         'user: root question\n\nassistant: branch A\n\nassistant: branch B')

    def test_incremental_updates_and_override_survive(self):
        entries = [self.header, self.message('user', 'first')]
        self.write(entries)
        self.pi_status()
        row = self.row()
        self.db.execute('INSERT INTO overrides VALUES (?, ?)', (row['key'], 'manual project'))
        self.db.execute("UPDATE sessions SET indexed_at='unchanged'")
        self.db.commit()
        self.assertIn('未变化 1', self.pi_status()[1])
        self.assertEqual(self.row()['indexed_at'], 'unchanged')
        self.write(entries + [self.message('assistant', 'new answer')])
        self.assertIn('更新 1', self.pi_status()[1])
        self.assertEqual(self.row()['rowid'], row['rowid'])
        self.assertIn('new answer', self.row()['content'])
        self.assertEqual(self.db.execute('SELECT project FROM overrides').fetchone()[0], 'manual project')
        self.assertEqual(self.db.execute("SELECT count(*) FROM sessions WHERE agent='pi'").fetchone()[0], 1)

    def test_malformed_records_preserve_old_index(self):
        self.write([self.header, self.message('user', 'keep original')])
        self.pi_status()
        original = dict(self.row())
        bad_records = [
            [], [None], [[]], ['scalar'], [self.message('user', 'missing header')],
            [dict(self.header, id='')], [dict(self.header, id=123)],
            [dict(self.header, cwd=[])],
            [dict(self.header, version=4)], [dict(self.header, version='3')],
            [dict(self.header, version=True)], [self.header, self.header],
            [self.header, dict(type='message', message=[])],
            [self.header, dict(type='session_info', name=42)],
        ]
        for entries in bad_records:
            with self.subTest(entries=entries):
                self.write(entries)
                self.assertEqual(self.pi_status()[0], 'partial')
                self.assertEqual(dict(self.row()), original)
        for content in (b'{bad json\n', b'\xff\n'):
            with self.subTest(content=content):
                self.file.write_bytes(content)
                self.assertEqual(self.pi_status()[0], 'partial')
                self.assertEqual(dict(self.row()), original)

    def test_one_bad_file_does_not_block_other_sessions(self):
        self.file.write_text('{truncated', encoding='utf8')
        other = self.file.with_name('other.jsonl')
        self.write([dict(self.header, id='other-session'), self.message('user', 'healthy')], other)
        self.file.with_name('ignored.json').write_text('{}', encoding='utf8')
        self.file.with_name('ignored.jsonl.zst').write_bytes(b'not a Pi session')
        status, detail = self.pi_status()
        self.assertEqual(status, 'partial')
        self.assertIn('更新 1', detail)
        self.assertIn('失败 1', detail)
        self.assertEqual(self.row()['native_id'], 'other-session')

    def test_blank_lines_and_crlf(self):
        self.write([self.header, self.message('user', 'hello')])
        self.file.write_bytes(b'\r\n' + self.file.read_bytes().replace(b'\n', b'\r\n\r\n'))
        self.assertEqual(parse_jsonl(self.file, 'pi')['content'], 'user: hello')

    def test_missing_and_wrong_source_preserve_index(self):
        self.write([self.header, self.message('user', 'keep')])
        self.pi_status()
        self.roots['pi'] = self.file
        self.assertEqual(self.pi_status()[0], 'error')
        self.roots['pi'] = self.root / 'missing'
        self.assertEqual(self.pi_status()[0], 'missing')
        self.assertEqual(self.row()['content'], 'user: keep')

    def test_default_path_and_environment_precedence(self):
        with patch('harnession.core.Path.home', return_value=self.root):
            for agent_dir, session_dir, expected in (
                ('', '', self.root / '.pi' / 'agent' / 'sessions'),
                ('~/custom-agent', '', self.root / 'custom-agent' / 'sessions'),
                ('~/custom-agent', '~/custom-sessions', self.root / 'custom-sessions'),
                ('', str(self.root / 'absolute-sessions'), self.root / 'absolute-sessions'),
            ):
                with self.subTest(agent_dir=agent_dir, session_dir=session_dir), patch.dict(
                    os.environ, {'PI_CODING_AGENT_DIR': agent_dir,
                                 'PI_CODING_AGENT_SESSION_DIR': session_dir,
                                 'HOME': str(self.root), 'USERPROFILE': str(self.root)}
                ):
                    self.assertEqual(defaults()['pi'], expected)

    def test_cli_scan_status_search_show_and_assign(self):
        self.write([self.header, self.message('user', 'CLI 中文 needle')])
        env = dict(PI_CODING_AGENT_DIR=str(self.root / 'unused-agent'),
                   PI_CODING_AGENT_SESSION_DIR=str(self.root / 'unused-sessions'))
        with patch('harnession.__main__.defaults', return_value=dict(self.roots, pi=self.root / 'unused')), patch.dict(os.environ, env):
            code, output = self.cli('scan', '--pi', str(self.roots['pi']))
        self.assertEqual(code, 0)
        self.assertIn('pi: ok', output)
        self.assertIn('pi: ok', self.cli('status')[1])
        self.assertIn(str(self.roots['pi']), self.cli('status')[1])
        self.assertIn('CLI 中文 needle', self.cli('list', '--agent', 'pi', '--search', '中文')[1])
        self.assertIn('CLI 中文 needle', self.cli('list', '--agent', 'pi', '--project', '中文项目')[1])
        self.assertEqual(self.cli('list', '--agent', 'claude', '--search', 'needle')[1], '')
        rowid = str(self.row()['rowid'])
        code, output = self.cli('show', rowid)
        self.assertEqual(code, 0)
        self.assertIn('原始 ID: pi-session-id', output)
        self.assertIn('user: CLI 中文 needle', output)
        assigned = self.root / 'assigned'
        self.assertEqual(self.cli('assign', rowid, str(assigned))[0], 0)
        self.assertIn(str(assigned), self.cli('projects')[1])
        self.assertEqual(self.cli('assign', rowid, '--reset')[0], 0)
        self.assertIn(str(self.project), self.cli('projects')[1])

    def test_cli_reports_partial_failure(self):
        self.file.write_text('{bad', encoding='utf8')
        with patch('harnession.__main__.defaults', return_value=self.roots):
            code, output = self.cli('scan')
        self.assertEqual(code, 1)
        self.assertIn('pi: partial', output)


if __name__ == '__main__':
    unittest.main()
