import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from harnession.core import parse_jsonl
from harnession.events import parser_version
from harnession.gateway import Gateway
from harnession.instruction_blocks import split_instructions
from harnession.message_parts import message_events


AGENTS = '# AGENTS.md instructions for /demo\n\n<INSTRUCTIONS>\n合成仓库规则 RULE_NEEDLE\n</INSTRUCTIONS>'
CONTEXT = '<environment_context>\n<cwd>/demo</cwd>\n</environment_context>'
ROLE = '# Role\n你是一个合成测试助手。\n'
SKILL = ('Base directory for this skill: /demo/.claude/skills/codebase-memory\n\n'
         '# Codebase Memory\n合成技能说明 SKILL_NEEDLE。\n\n'
         '## Workflow\n1. 读取代码。\n\n```sh\nprintf "demo"\n```\n')
CLAUDE_CAVEAT = ('<local-command-caveat>The command below was run directly in Claude Code, '
                 'not sent to you as a request, and its output goes straight to the user. '
                 'It is recorded here for reference.</local-command-caveat>')
CLAUDE_COMMAND = ('<command-name>/example</command-name><command-message>example</command-message>'
                  '<command-args>demo</command-args>')


class InstructionBlockTests(unittest.TestCase):
    def test_skill_document_is_one_instruction_including_headings_and_fenced_examples(self):
        parts = split_instructions(SKILL)
        self.assertEqual(parts, [dict(text=SKILL, category='skill', label='技能 · codebase-memory')])
        events = message_events({'id': 'skill-u'}, {'content': SKILL}, 'user', 1, '/demo')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['kind'], 'instruction')
        self.assertEqual(events[0]['metadata']['instruction_category'], 'skill')
        self.assertEqual(events[0]['text'], SKILL)
        self.assertEqual(message_events({'id': 'a'}, {'content': SKILL}, 'assistant', 1, '/demo')[0]['kind'], 'message')
        padded = message_events({'id': 'padded'}, {'content': [
            dict(type='text', text='\n\n' + SKILL),
            dict(type='image', source=dict(type='base64', media_type='image/png', data='aGVsbG8=')),
        ]}, 'user', 1, '/demo')
        self.assertEqual([event['kind'] for event in padded], ['instruction'])
        self.assertEqual(len(padded[0]['attachments']), 1)

    def test_skill_paths_and_consecutive_documents_keep_separate_labels_and_exact_text(self):
        for path in ['/demo/skills/my skill/', '~/skills/codebase-memory',
                     r'C:\Users\demo\.claude\skills\codebase-memory',
                     '`/demo/skills/codebase-memory`', r'\\server\skills\codebase-memory']:
            with self.subTest(path=path):
                parts = split_instructions(' \nBase directory for this skill: ' + path + '\r\n# Skill\n正文')
                instruction = next(part for part in parts if part['category'])
                self.assertEqual(instruction['category'], 'skill')
                self.assertIn('my skill' if 'my skill' in path else 'codebase-memory', instruction['label'])
        second = SKILL.replace('codebase-memory', 'review')
        text = AGENTS + '\n' + SKILL + second
        parts = split_instructions(text)
        self.assertEqual(''.join(part['text'] for part in parts), text)
        self.assertEqual([part['label'] for part in parts if part['category']],
                         ['AGENTS.md', '技能 · codebase-memory', '技能 · review'])

    def test_skill_mentions_quotes_and_code_examples_are_not_instructions(self):
        for text in [
            '请解释 Base directory for this skill: /demo/skills/example',
            '请解释以下内容：\n' + SKILL,
            '> Base directory for this skill: /demo/skills/example\n> 引用',
            '```text\n' + SKILL.replace('```', '~~~') + '```\n',
            'Base directory for this skill: 这个短语是什么意思？',
        ]:
            with self.subTest(text=text):
                self.assertFalse(any(part['category'] for part in split_instructions(text)))

    def test_explicit_wrappers_and_headings_are_separate_exact_text_slices(self):
        text = AGENTS + '\n' + CONTEXT + '\n' + ROLE + '\n# 用户请求\n请修复分页。'
        parts = split_instructions(text)
        self.assertEqual(''.join(item['text'] for item in parts), text)
        tagged = [item for item in parts if item['category']]
        self.assertEqual([item['category'] for item in tagged], ['agents', 'context', 'role'])
        ordinary = ''.join(item['text'] for item in parts if not item['category']).strip()
        self.assertEqual(ordinary, '# 用户请求\n请修复分页。')
        self.assertEqual(tagged[0]['text'], AGENTS)

    def test_mentions_quotes_code_and_prose_introduced_headings_stay_visible(self):
        for text in [
            '请解释 AGENTS.md instructions 的含义。',
            'Role 在这里如何设计？',
            '> # Role\n> 示例说明',
            '```md\n# AGENTS.md instructions\n<INSTRUCTIONS>example</INSTRUCTIONS>\n# Role\n```',
            '~~~xml\n<environment_context>example</environment_context>\n~~~',
            '请解释以下内容：\n# Role\n这个标题有什么作用？',
            '<role>缺少结束标签的示例',
        ]:
            with self.subTest(text=text):
                parts = split_instructions(text)
                self.assertFalse(any(item['category'] for item in parts))
                self.assertEqual(''.join(item['text'] for item in parts), text)

    def test_role_labels_and_nested_wrappers_respect_boundaries(self):
        text = '<role>合成角色</role>\n请实现标签。'
        parts = split_instructions(text)
        self.assertEqual(parts[0]['label'], 'Role')
        self.assertIn('请实现标签。', parts[-1]['text'])
        text = '# Role: 合成助手\n按约定行动。\n# Task\n最后提问'
        parts = split_instructions(text)
        self.assertEqual(parts[0]['category'], 'role')
        self.assertIn('最后提问', parts[-1]['text'])
        text = '<INSTRUCTIONS>\n<INSTRUCTIONS>nested</INSTRUCTIONS>\n```xml\n</INSTRUCTIONS>\n```\n规则\n</INSTRUCTIONS>\n真正问题'
        parts = split_instructions(text)
        self.assertEqual(parts[0]['category'], 'instructions')
        self.assertIn('规则', parts[0]['text'])
        self.assertEqual(parts[-1]['text'].strip(), '真正问题')
        text = AGENTS + CONTEXT + '<role>合成角色</role>\n真正问题'
        parts = split_instructions(text)
        self.assertEqual([item['category'] for item in parts if item['category']], ['agents', 'context', 'role'])
        self.assertEqual(parts[-1]['text'].strip(), '真正问题')
        self.assertEqual(''.join(item['text'] for item in parts), text)

    def test_mixed_message_retains_question_identity_parent_and_one_image(self):
        content = [
            {'type': 'text', 'text': AGENTS + '\n' + CONTEXT + '\n请修改最后一个问题。'},
            {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'aGVsbG8='}},
        ]
        events = message_events({'id': 'u', 'parentId': 'previous'}, {'content': content}, 'user', 7, '/demo')
        self.assertEqual([event['kind'] for event in events], ['instruction', 'instruction', 'message'])
        self.assertIsNone(events[-1]['identity_suffix'])
        self.assertEqual(events[-1]['source_event_id'], 'u')
        self.assertEqual(events[-1]['parent_event_id'], 'previous')
        self.assertEqual(sum(len(event['attachments']) for event in events), 1)
        self.assertEqual(len(events[-1]['attachments']), 1)
        assistant = message_events({'id': 'a'}, {'content': AGENTS}, 'assistant', 8, '/demo')
        self.assertEqual(assistant[0]['kind'], 'message')


class InstructionSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.roots = {agent: self.root / agent for agent in ('codex', 'claude', 'pi', 'opencode')}
        self.roots['hermes'] = self.root / 'state.db'
        for agent in ('codex', 'claude', 'pi'):
            self.roots[agent].mkdir()
        self.path = self.roots['claude'] / 'session.jsonl'
        self.db_path = self.root / 'index.sqlite3'
        self.gateway = Gateway(self.db_path)
        self.addCleanup(self.gateway.close)

    def write(self, texts):
        records = [dict(type=role, sessionId='native-session', uuid=str(i),
                        parentUuid=str(i - 1) if i else None, message=dict(content=text))
                   for i, (role, text) in enumerate(texts)]
        self.path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records))
        self.gateway.scan_sources({agent: str(path) for agent, path in self.roots.items()})
        return self.gateway.list_sessions()['items'][0]

    def test_excerpt_is_latest_real_user_question_even_after_trailing_instructions_and_tools(self):
        session = self.write([
            ('user', AGENTS), ('user', '第一个问题'), ('assistant', '第一条回答'),
            ('user', ROLE), ('user', CONTEXT + '\n最后一次用户提问 LATEST_NEEDLE'),
            ('assistant', '最后回复不作为摘要'), ('user', AGENTS),
            ('user', [{'type': 'tool_result', 'tool_use_id': 'c', 'content': '不是用户提问'}]),
        ])
        self.assertEqual(session['excerpt'], '最后一次用户提问 LATEST_NEEDLE')
        self.assertEqual(session['title'], '第一个问题')
        self.assertEqual(len(self.gateway.list_sessions(query='RULE_NEEDLE')['items']), 1)
        located = self.gateway.read_events(session['id'], query='RULE_NEEDLE', limit=1)
        self.assertEqual(located['items'][0]['kind'], 'instruction')
        self.assertEqual(located['items'][0]['metadata']['instruction_category'], 'agents')
        ids, cursor = [], 0
        while True:
            page = self.gateway.read_events(session['id'], cursor=cursor, limit=1)
            ids.extend(item['id'] for item in page['items'])
            if page['next_cursor'] is None:
                break
            cursor = page['next_cursor']
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 9)

    def test_instruction_only_sessions_have_no_question_preview(self):
        session = self.write([('user', AGENTS), ('user', ROLE), ('assistant', '合成回复')])
        self.assertEqual(session['excerpt'], '')
        self.assertEqual(session['title'], 'native-session')

    def test_claude_local_commands_are_folded_and_excluded_from_title_and_excerpt(self):
        session = self.write([
            ('user', CLAUDE_CAVEAT), ('user', CLAUDE_COMMAND),
            ('user', [dict(type='text', text='<local-command-stdout>OUTPUT_NEEDLE</local-command-stdout>'),
                      dict(type='text', text='第一个真实问题')]),
            ('user', '最近一次提问'), ('user', CLAUDE_COMMAND),
        ])
        self.assertEqual(session['title'], '第一个真实问题')
        self.assertEqual(session['excerpt'], '最近一次提问')
        found = self.gateway.read_events(session['id'], query='OUTPUT_NEEDLE', limit=1)['items'][0]
        self.assertEqual(found['kind'], 'instruction')
        self.assertEqual(found['metadata']['instruction_label'], '本地命令输出')
        self.assertEqual(self.gateway.list_sessions(query='OUTPUT_NEEDLE')['items'][0]['id'], session['id'])
        self.assertEqual(self.gateway.read_events(session['id'])['items'][0]['text'], CLAUDE_CAVEAT)

    def test_claude_command_only_session_has_a_fallback_title_and_no_excerpt(self):
        session = self.write([('user', CLAUDE_CAVEAT), ('user', CLAUDE_COMMAND), ('system', 'command output')])
        self.assertEqual(session['title'], '本地命令会话')
        self.assertEqual(session['excerpt'], '')

    def test_claude_command_markup_inside_code_examples_remains_user_text(self):
        text = '```xml\n' + CLAUDE_COMMAND + '\n```\n这些标签是什么意思？'
        session = self.write([('user', text)])
        self.assertEqual(self.gateway.read_events(session['id'])['items'][0]['text'], text)
        self.assertEqual(session['excerpt'], text)

    def test_gateway_reopen_replaces_truncated_claude_command_title_from_v4(self):
        session = self.write([('user', CLAUDE_CAVEAT), ('user', '真实问题'), ('user', CLAUDE_COMMAND)])
        key = self.gateway.session(session['id'])['key']
        question = next(event for event in self.gateway.read_events(session['id'])['items'] if event['kind'] == 'message')
        self.gateway.db.execute('UPDATE sessions SET title=? WHERE key=?', (CLAUDE_CAVEAT[:160], key))
        self.gateway.db.execute('UPDATE event_state SET parser_version=4 WHERE session_key=?', (key,))
        self.gateway.db.execute("UPDATE session_events SET kind='message', metadata='{}' WHERE session_key=?", (key,))
        self.gateway.db.execute('INSERT INTO overrides VALUES (?,?)', (key, 'manual-project'))
        self.gateway.db.commit()
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        reopened = Gateway(self.db_path)
        self.addCleanup(reopened.close)
        updated = reopened.list_sessions()['items'][0]
        self.assertEqual((updated['id'], updated['title'], updated['excerpt'], updated['project']),
                         (session['id'], '真实问题', '真实问题', 'manual-project'))
        self.assertEqual(next(event for event in reopened.read_events(session['id'])['items']
                              if event['kind'] == 'message'), question)
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns))

    def test_last_question_includes_all_visible_fragments_and_ignores_blank_messages(self):
        session = self.write([
            ('user', '第一次提问'), ('assistant', '回复'),
            ('user', '请修复分页。\n' + CONTEXT + '\n同时保持搜索可用。'),
            ('user', '\n\t  '),
        ])
        self.assertEqual(session['excerpt'], '请修复分页。\n同时保持搜索可用。')
        self.assertNotIn('environment_context', session['excerpt'])

    def test_gateway_reopen_upgrades_v2_without_modifying_source_or_manual_project(self):
        session = self.write([('user', AGENTS), ('user', '保留真实问题')])
        events = self.gateway.read_events(session['id'])['items']
        key = self.gateway.db.execute('SELECT key FROM sessions').fetchone()[0]
        self.gateway.db.execute('UPDATE session_events SET kind="message",metadata="{}"')
        self.gateway.db.execute('UPDATE event_state SET parser_version=2')
        self.gateway.db.execute('INSERT INTO overrides VALUES (?,?)', (key, 'manual-project'))
        self.gateway.db.commit()
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        reopened = Gateway(self.db_path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get_session(session['id'])['project'], 'manual-project')
        self.assertEqual(reopened.read_events(session['id'])['items'], events)
        self.assertEqual(reopened.list_sessions()['items'][0]['excerpt'], '保留真实问题')
        self.assertEqual(reopened.db.execute('SELECT parser_version FROM event_state').fetchone()[0], parser_version('claude'))
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns))

    def test_skill_messages_are_searchable_but_not_previews_and_upgrade_existing_v3(self):
        session = self.write([('user', '真实用户提问'), ('assistant', '合成回复'), ('user', SKILL)])
        events = self.gateway.read_events(session['id'])['items']
        skill = events[-1]
        self.assertEqual(skill['kind'], 'instruction')
        self.assertEqual(skill['metadata']['instruction_label'], '技能 · codebase-memory')
        self.assertEqual(self.gateway.list_sessions()['items'][0]['excerpt'], '真实用户提问')
        self.assertEqual(len(self.gateway.list_sessions(query='SKILL_NEEDLE')['items']), 1)
        self.assertEqual(self.gateway.read_events(session['id'], query='SKILL_NEEDLE', limit=1)['items'][0], skill)
        key = self.gateway.db.execute('SELECT key FROM sessions').fetchone()[0]
        self.gateway.db.execute('UPDATE session_events SET kind="message",metadata="{}" WHERE id=?', (skill['id'],))
        self.gateway.db.execute('UPDATE event_state SET parser_version=3')
        self.gateway.db.execute('INSERT INTO overrides VALUES (?,?)', (key, 'manual-skill-project'))
        self.gateway.db.commit()
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        reopened = Gateway(self.db_path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.read_events(session['id'])['items'], events)
        self.assertEqual(reopened.get_session(session['id'])['project'], 'manual-skill-project')
        self.assertEqual(reopened.list_sessions()['items'][0]['excerpt'], '真实用户提问')
        self.assertEqual(reopened.db.execute('SELECT parser_version FROM event_state').fetchone()[0], parser_version('claude'))
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns))

    def test_codex_pi_and_hermes_apply_same_instruction_classification(self):
        for agent in ('codex', 'pi'):
            with self.subTest(agent=agent):
                records = ([dict(type='session_meta', payload=dict(id='native'))] if agent == 'codex'
                           else [dict(type='session', id='native', version=3)])
                message = dict(role='user', content=[dict(type='text', text=AGENTS + '\n真实提问')])
                records.append(dict(type='response_item', payload=dict(type='message', **message)) if agent == 'codex'
                               else dict(type='message', id='u', message=message))
                path = self.roots[agent] / 'session.jsonl'
                path.write_text(''.join(json.dumps(row) + '\n' for row in records))
                events = parse_jsonl(path, agent)['events']
                self.assertEqual([event['kind'] for event in events], ['instruction', 'message'])
        source = sqlite3.connect(self.roots['hermes'])
        source.executescript('CREATE TABLE sessions(id TEXT,cwd TEXT,started_at REAL,last_activity_at REAL,title TEXT); CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,content TEXT);')
        source.execute('INSERT INTO sessions VALUES ("h",NULL,1,2,NULL)')
        source.execute('INSERT INTO messages VALUES (1,"h","user",?)', (AGENTS,))
        source.execute('INSERT INTO messages VALUES (2,"h","user","Hermes 最新问题")')
        source.commit()
        source.close()
        self.gateway.scan_sources({agent: str(path) for agent, path in self.roots.items()})
        session = self.gateway.list_sessions(agent='hermes')['items'][0]
        self.assertEqual(session['excerpt'], 'Hermes 最新问题')
        self.assertEqual(self.gateway.read_events(session['id'])['items'][0]['kind'], 'instruction')
