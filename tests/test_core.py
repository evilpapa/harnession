import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from harnession.core import connect, scan, project_for
from harnession.__main__ import main


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dbpath = self.root / 'index.db'
        self.db = connect(self.dbpath)
        self.roots = {x: self.root / x for x in ('codex','claude','pi','opencode')}
        self.roots['hermes'] = self.root / 'state.db'
        self.roots['claude'].mkdir()
        self.file = self.roots['claude'] / 'session.jsonl'
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / '.git').mkdir()
        self.write_records()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def write_records(self):
        records = [dict(type='user', sessionId='one', cwd=str(self.project), timestamp='2026-01-01', message=dict(role='user', content='find the needle')),
                   dict(type='assistant', sessionId='one', message=dict(role='assistant', content=[dict(type='text',text='answer')]))]
        self.file.write_text(''.join(json.dumps(x)+'\n' for x in records),encoding='utf8')

    def test_incremental_and_override_survives(self):
        scan(self.db,self.roots)
        row = self.db.execute('select rowid,* from sessions').fetchone()
        self.assertEqual(row['project'],str(self.project))
        self.assertIn('find the needle',row['content'])
        self.db.execute('insert into overrides values (?,?)',(row['key'],'custom'))
        self.db.commit()
        result = scan(self.db,self.roots)
        self.assertIn('未变化 1',dict((a,d) for a,_,d in result)['claude'])
        self.write_records()
        scan(self.db,self.roots)
        self.assertEqual(self.db.execute('select count(*) from sessions').fetchone()[0],1)
        self.assertEqual(self.db.execute('select project from overrides').fetchone()[0],'custom')

    def test_bad_source_preserves_index(self):
        scan(self.db,self.roots)
        self.file.write_text('{bad json\n',encoding='utf8')
        result = scan(self.db,self.roots)
        self.assertEqual(dict((a,s) for a,s,_ in result)['claude'],'partial')
        self.assertIn('needle',self.db.execute('select content from sessions').fetchone()[0])

    def test_wrong_source_type_is_error(self):
        scan(self.db, self.roots)
        self.roots['claude'] = self.file
        self.roots['hermes'] = self.root
        result = scan(self.db, self.roots)
        statuses = {agent: status for agent, status, _ in result}
        self.assertEqual(statuses['claude'], 'error')
        self.assertEqual(statuses['hermes'], 'error')
        self.assertIn('needle', self.db.execute('select content from sessions').fetchone()[0])

    def test_hermes_sqlite(self):
        source = sqlite3.connect(str(self.roots['hermes']))
        source.executescript('create table sessions(id text,cwd text,started_at real,last_activity_at real,title text); create table messages(id integer,session_id text,role text,content text);')
        source.execute('insert into sessions values (?,?,?,?,?)',('h1',str(self.project),1,2,'hello'))
        source.execute('insert into messages values (?,?,?,?)',(1,'h1','user','hermes message'))
        source.commit(); source.close()
        scan(self.db,self.roots)
        self.assertIn('hermes message',self.db.execute("select content from sessions where agent='hermes'").fetchone()[0])

    def test_worktree_project(self):
        gitdir = self.project / '.git' / 'worktrees' / 'feature'
        gitdir.mkdir(parents=True)
        tree = self.root / 'feature'; tree.mkdir()
        (tree / '.git').write_text('gitdir: '+str(gitdir),encoding='utf8')
        self.assertEqual(project_for(str(tree)),str(self.project))

    def test_cli_missing_id(self):
        self.assertEqual(main(['--db',str(self.dbpath),'show','999']),1)

if __name__ == '__main__': unittest.main()
