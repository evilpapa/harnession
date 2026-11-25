from pathlib import Path
import shlex
import tempfile
import unittest

from harnession.deletion import deletion_recipe, resume_recipe


class DeletionRecipeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.session_id = '019a8836-b93f-7a51-9be7-791ed467b370'

    def file(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic session', encoding='utf8')
        return path

    def test_codex_command_uses_native_uuid_and_correct_home(self):
        path = self.file('codex home/sessions/2026/rollout.jsonl')
        result = deletion_recipe('codex', self.session_id, str(path), self.root / 'codex home/sessions')
        self.assertEqual(shlex.split(result['command']), [
            'env', 'CODEX_HOME=' + str(self.root / 'codex home'), 'codex', 'delete', '--', self.session_id])
        self.assertEqual(result['command'], result['native_command'])
        self.assertEqual(result['method'], 'native')
        self.assertEqual(shlex.split(result['file_command']), ['rm', '--', str(path)])
        self.assertEqual(path.read_text(encoding='utf8'), 'synthetic session')

    def test_codex_nested_source_root_keeps_native_delete(self):
        path = self.file('codex/sessions/2026/11/rollout.jsonl')
        result = deletion_recipe('codex', self.session_id, str(path), path.parent)
        self.assertEqual(shlex.split(result['command']),
                         ['env', 'CODEX_HOME=' + str(self.root / 'codex'),
                          'codex', 'delete', '--', self.session_id])

    def test_file_agents_resolve_one_exact_file_and_shell_quote_it(self):
        for agent in ('claude', 'pi', 'codex'):
            with self.subTest(agent=agent):
                path = self.file(f"{agent}/中文 '$(touch nope);.jsonl")
                result = deletion_recipe(agent, 'original-id', str(path), path.parent)
                self.assertEqual(shlex.split(result['command']), ['rm', '--', str(path)])
                self.assertEqual(result['session_id'], 'original-id')
                self.assertEqual(result['method'], 'file')
                self.assertIsNone(result['native_command'])
                self.assertTrue(result['native_reason'])
                self.assertEqual(path.read_text(encoding='utf8'), 'synthetic session')

    def test_hermes_deletes_one_id_without_deleting_shared_database(self):
        path = self.file('hermes profile/state.db')
        result = deletion_recipe('hermes', self.session_id, str(path) + '#' + self.session_id, path)
        self.assertEqual(shlex.split(result['command']), [
            'env', 'HERMES_HOME=' + str(path.parent), 'hermes', 'sessions', 'delete', '--', self.session_id])
        self.assertEqual(path.read_text(encoding='utf8'), 'synthetic session')
        self.assertIsNone(result['file_command'])
        self.assertIsNone(deletion_recipe('hermes', 'other-id', str(path) + '#' + self.session_id, path)['command'])
        custom = self.file('hermes/custom.db')
        self.assertIsNone(deletion_recipe('hermes', 'id', str(custom) + '#id', custom)['command'])

    def test_unavailable_and_unsafe_targets_do_not_produce_commands(self):
        path = self.file('claude/session.jsonl')
        unsafe = self.file('claude/session[copy].jsonl')
        link = self.root / 'link.jsonl'
        try:
            link.symlink_to(path)
        except (OSError, NotImplementedError):
            self.skipTest('当前环境不允许创建符号链接（Windows 需启用开发者模式或以管理员身份运行）')
        for agent, session_id, source, root in [
            ('opencode', 'id', str(path), path.parent),
            ('claude', '', str(path), path.parent),
            ('claude', 'id\ncommand', str(path), path.parent),
            ('claude', 'id', '', path.parent),
            ('claude', 'id', str(path), None),
            ('claude', 'id', str(path), self.root / 'elsewhere'),
            ('claude', 'id', str(path.parent), path.parent),
            ('claude', 'id', str(path) + '.missing', path.parent),
            ('claude', 'id', 'relative.jsonl', path.parent),
            ('claude', 'id', str(link), self.root),
            ('claude', 'id', str(unsafe), path.parent),
        ]:
            with self.subTest(agent=agent, source=source, session_id=session_id):
                result = deletion_recipe(agent, session_id, source, root)
                self.assertIsNone(result['command'])
                self.assertTrue(result['reason'])

    def test_resume_codex_uses_native_id_profile_and_working_directory(self):
        path = self.file('codex home/sessions/2026/rollout.jsonl')
        result = resume_recipe('codex', self.session_id, str(path), path.parent, str(self.root))
        self.assertEqual(shlex.split(result['command']),
                         ['env', 'CODEX_HOME=' + str(self.root / 'codex home'),
                          'codex', '--cd', str(self.root), 'resume', '--', self.session_id])
        self.assertEqual(path.read_text(encoding='utf8'), 'synthetic session')

    def test_resume_claude_preserves_config_and_shell_quotes_working_directory(self):
        path = self.file('claude home/projects/project/session.jsonl')
        cwd = self.root / "中文 '$(touch nope); project"
        cwd.mkdir()
        result = resume_recipe('claude', self.session_id, str(path), path.parent, str(cwd))
        self.assertEqual(shlex.split(result['command']),
                         ['cd', '--', str(cwd), '&&', 'env',
                          'CLAUDE_CONFIG_DIR=' + str(self.root / 'claude home'),
                          'claude', '--resume', self.session_id])
        self.assertEqual(path.read_text(encoding='utf8'), 'synthetic session')

    def test_resume_pi_uses_exact_source_and_hermes_uses_database_profile(self):
        path = self.file('pi/agent/sessions/project/session.jsonl')
        result = resume_recipe('pi', 'pi-native-id', str(path), path.parent)
        self.assertEqual(shlex.split(result['command']),
                         ['env', 'PI_CODING_AGENT_DIR=' + str(self.root / 'pi/agent'),
                          'pi', '--session-dir', str(path.parent), '--session', str(path)])
        path = self.file('hermes profile/state.db')
        result = resume_recipe('hermes', 'hermes-native-id', str(path) + '#hermes-native-id', path)
        self.assertEqual(shlex.split(result['command']),
                         ['env', 'HERMES_HOME=' + str(path.parent), 'hermes', '--resume', 'hermes-native-id'])

    def test_resume_rejects_missing_sources_wrong_profiles_and_option_ids(self):
        path = self.file('imported/session.jsonl')
        for agent, native_id, source, root, cwd in [
            ('codex', self.session_id, str(path), path.parent, None),
            ('claude', self.session_id, str(path), path.parent, None),
            ('pi', '--new', str(path), path.parent, None),
            ('pi', 'id', str(path), path.parent, str(self.root / 'missing')),
            ('pi', 'id', str(path) + '.missing', path.parent, None),
            ('pi', 'id', str(path), self.root / 'other-source', None),
            ('hermes', 'other', str(path) + '#id', path, None),
            ('opencode', 'id', str(path), path.parent, None),
        ]:
            with self.subTest(agent=agent, native_id=native_id, source=source):
                result = resume_recipe(agent, native_id, source, root, cwd)
                self.assertIsNone(result['command'])
                self.assertTrue(result['reason'])


if __name__ == '__main__':
    unittest.main()
