"""Local desktop API. JSONL on stdin/stdout; no human CLI-output parsing."""

import argparse
import base64
import json
import mimetypes
from pathlib import Path
import sqlite3
import sys

from .core import connect, data_dir, defaults, file_fingerprint, refresh_stale_events, scan
from .events import IMAGE_TYPES, MAX_IMAGE_BYTES, parser_version
from .locking import writer_lock
from .deletion import deletion_recipe, direct_target, remove_session_file, resume_recipe
from .codex_titles import refresh_codex_titles

PROTOCOL_VERSION = 1


class Gateway:
    def __init__(self, path, *, allow_delete=False):
        self.db = connect(path)
        self.allow_delete = allow_delete
        with writer_lock(self.db):
            refresh_stale_events(self.db)
            source = self.db.execute("SELECT path FROM sources WHERE agent='codex'").fetchone()
            refresh_codex_titles(self.db, source['path'] if source and source['path'] else self.roots()['codex'])
            self.db.commit()

    def close(self):
        self.db.close()

    def generation(self):
        row = self.db.execute('SELECT count(*),coalesce(max(indexed_at),"") FROM sessions').fetchone()
        return str(row[0]) + ':' + row[1]

    def roots(self):
        roots = defaults()
        for row in self.db.execute("SELECT key,value FROM ui_settings WHERE key LIKE 'source.%'"):
            agent = row['key'][7:]
            if agent in roots:
                roots[agent] = Path(row['value']).expanduser()
        return roots

    @staticmethod
    def limit(value):
        if type(value) is not int or not 1 <= value <= 200:
            raise ValueError('limit 必须为 1–200')
        return value

    def session(self, session_id):
        row = self.db.execute('''SELECT s.*,COALESCE(o.project,s.project) AS effective_project,
            e.session_id,e.parser_version FROM sessions s
            JOIN event_state e ON e.session_key=s.key
            LEFT JOIN overrides o ON o.key=s.key WHERE e.session_id=?''', (session_id,)).fetchone()
        if row is None:
            raise ValueError('会话不存在')
        return row

    @staticmethod
    def summary(row):
        return dict(id=row['session_id'], agent=row['agent'], title=row['title'] or '无标题',
                    project=row['effective_project'], cwd=row['cwd'], created=row['created'],
                    updated=row['updated'], indexed_at=row['indexed_at'],
                    excerpt=row['excerpt'] if 'excerpt' in row.keys() else '',
                    structured=bool(row['parser_version']))

    def dispatch(self, method, params=None):
        params = params or {}
        if not isinstance(params, dict):
            raise ValueError('params 应为对象')
        handlers = {
            'bootstrap': self.bootstrap, 'list_sessions': self.list_sessions,
            'get_session': self.get_session, 'read_events': self.read_events,
            'scan_sources': self.scan_sources, 'read_artifact': self.read_artifact,
        }
        if self.allow_delete:
            handlers['delete_session'] = self.delete_session
        if method not in handlers:
            raise ValueError('不支持的方法')
        return handlers[method](**params)

    def bootstrap(self):
        home = str(Path.home())
        rows = self.db.execute('''SELECT COALESCE(o.project,s.project) AS path,count(*) AS count
            FROM sessions s LEFT JOIN overrides o ON o.key=s.key GROUP BY path ORDER BY count DESC,path''')
        projects = [dict(id=row['path'], name=('~' if Path(row['path']) == Path(home) else Path(row['path']).name) if row['path'] else '未归类',
                         path=row['path'], count=row['count']) for row in rows]
        sources = {row['agent']: dict(row) for row in self.db.execute('SELECT * FROM sources')}
        for agent, path in self.roots().items():
            if agent not in sources:
                sources[agent] = dict(agent=agent, path=str(path), status='unsupported' if agent == 'opencode' else 'unscanned',
                                      detail='适配器尚未实现' if agent == 'opencode' else '尚未扫描', scanned_at=None)
        counts = {row['agent']: row['n'] for row in self.db.execute('SELECT agent,count(*) AS n FROM sessions GROUP BY agent')}
        return dict(protocol_version=PROTOCOL_VERSION, generation=self.generation(), projects=projects,
                    sources=list(sources.values()), counts=counts, total=sum(counts.values()), home_directory=home)

    def list_sessions(self, project=None, agent=None, query='', cursor=0, limit=50, generation=None):
        limit = self.limit(limit)
        if type(cursor) is not int or cursor < 0:
            raise ValueError('cursor 无效')
        if generation is not None and generation != self.generation():
            return dict(items=[], next_cursor=None, generation=self.generation(), reset=True)
        sql = '''SELECT s.key,s.agent,s.title,s.cwd,s.created,s.updated,s.indexed_at,
            COALESCE(o.project,s.project) AS effective_project,
            e.session_id,e.parser_version FROM sessions s JOIN event_state e ON e.session_key=s.key
            LEFT JOIN overrides o ON o.key=s.key WHERE 1=1'''
        values = []
        if project is not None:
            sql += ' AND COALESCE(o.project,s.project,?)=?'
            values += ['', project]
        if agent:
            sql += ' AND s.agent=?'
            values.append(agent)
        if query:
            sql += ' AND (instr(COALESCE(s.title,?),?)>0 OR instr(COALESCE(s.content,?),?)>0)'
            values += ['', query, '', query]
        sql += ' ORDER BY COALESCE(s.updated,s.created,s.indexed_at) DESC,s.key LIMIT ? OFFSET ?'
        rows = list(self.db.execute(sql, [*values, limit + 1, cursor]))
        return dict(items=[dict(self.summary(row), excerpt=self.latest_question(row['key'])) for row in rows[:limit]],
                    next_cursor=cursor + limit if len(rows) > limit else None,
                    generation=self.generation(), reset=False)

    def latest_question(self, session_key):
        """Join ordinary fragments from the latest user record, omitting context."""
        parts, identity = [], None
        cursor = self.db.execute('''SELECT source_event_id,source_locator,text FROM session_events
            WHERE session_key=? AND kind='message' AND role='user'
              AND trim(text,char(9)||char(10)||char(13)||' ')!=''
            ORDER BY seq DESC''', (session_key,))
        try:
            for event in cursor:
                record = event['source_event_id'] or event['source_locator'].split('#part:', 1)[0]
                if identity is not None and record != identity:
                    break
                identity = record
                parts.append(event['text'].strip())
        finally:
            cursor.close()
        return '\n'.join(reversed(parts))[:180]

    def get_session(self, session_id):
        row = self.session(session_id)
        result = self.summary(row)
        source_value = row['source'] or ''
        source_path = Path(source_value.rsplit('#', 1)[0] if row['agent'] == 'hermes' else source_value)
        source = self.db.execute('SELECT status,detail,path FROM sources WHERE agent=?', (row['agent'],)).fetchone()
        result.update(source=row['source'], native_id=row['native_id'],
                      source_available=source_path.is_file(),
                      source_status=source['status'] if source else 'unscanned',
                      source_detail=source['detail'] if source else '',
                      coverage=('已收集来源中可读取的对话、工具调用/输出、思考记录及系统提示；技术消息默认折叠。未保存或加密的内容无法还原。'
                                if row['parser_version'] >= parser_version(row['agent']) else
                                '旧索引快照；点击“扫描本地会话”补采工具输出、思考记录及系统提示。'),
                      branch_status='活动路径未知；按来源记录顺序展示所有可见分支' if row['agent'] == 'pi' else None)
        root = source['path'] if source else self.roots().get(row['agent'])
        result['deletion'] = deletion_recipe(row['agent'], row['native_id'], row['source'], root)
        result['deletion']['direct_reason'] = self.direct_delete_reason(row, root)
        result['resumption'] = resume_recipe(row['agent'], row['native_id'], row['source'], root, row['cwd'])
        result['artifacts'] = [self.artifact_summary(item) for item in self.db.execute(
            'SELECT * FROM artifacts WHERE session_key=?', (row['key'],))]
        return result

    def direct_delete_reason(self, row, root):
        if not self.allow_delete:
            return '直接删除仅在桌面应用中可用。'
        try:
            direct_target(row['agent'], row['native_id'], row['source'], root)
        except (ValueError, OSError, RuntimeError) as error:
            return str(error)
        return None

    def delete_session(self, session_id, indexed_at):
        with writer_lock(self.db), self.db:
            row = self.session(session_id)
            if row['indexed_at'] != indexed_at:
                raise ValueError('会话索引已更新，请重新打开会话确认删除。')
            source = self.db.execute('SELECT path FROM sources WHERE agent=?', (row['agent'],)).fetchone()
            root = source['path'] if source else self.roots().get(row['agent'])
            path = direct_target(row['agent'], row['native_id'], row['source'], root)
            if self.db.execute('SELECT 1 FROM sessions WHERE source=? AND key!=?',
                               (str(path), row['key'])).fetchone():
                raise ValueError('此文件被多个会话索引引用，无法直接删除。')
            file_stat = path.lstat()
            if row['fingerprint'] != file_fingerprint(file_stat, row['agent']):
                raise ValueError('原始会话文件已更新，请扫描后重新确认。')
            # Stage the cache writes first. Any failed file operation rolls them back.
            for table in ('session_events', 'event_state', 'artifacts'):
                self.db.execute(f'DELETE FROM {table} WHERE session_key=?', (row['key'],))
            self.db.execute('DELETE FROM overrides WHERE key=?', (row['key'],))
            self.db.execute('DELETE FROM sessions WHERE key=?', (row['key'],))
            remove_session_file(path, file_stat)
            try:
                self.db.commit()
            except sqlite3.Error as error:
                raise ValueError('原始文件已删除，但清理本地索引失败。') from error
        return dict(deleted=True)

    @staticmethod
    def artifact_summary(row):
        available = not row['reason'] and (bool(row['embedded']) or bool(row['local_path'] and Path(row['local_path']).is_file()))
        return dict(id=row['id'], event_id=row['event_id'], name=row['name'], mime=row['mime'],
                    available=available, reason=row['reason'] or (None if available else '附件文件不可用'))

    def read_events(self, session_id, cursor=0, limit=100, query='', generation=None):
        limit = self.limit(limit)
        if type(cursor) is not int or cursor < 0:
            raise ValueError('cursor 无效')
        row = self.session(session_id)
        revision = row['indexed_at']
        if generation is not None and generation != revision:
            return dict(items=[], next_cursor=None, generation=revision, reset=True)
        if not row['parser_version']:
            items = [] if cursor else [dict(id='legacy-' + session_id, seq=1, role='archive',
                text=row['content'], timestamp=None, source_locator='legacy:index', parent_event_id=None,
                kind='message', metadata={}, attachments=[])]
            return dict(items=items, next_cursor=None, generation=revision, reset=False, legacy=True)
        located = False
        if query and not cursor:
            hit = self.db.execute('SELECT seq FROM session_events WHERE session_key=? AND instr(text,?)>0 ORDER BY seq LIMIT 1',
                                  (row['key'], query)).fetchone()
            if hit:
                cursor, located = max(0, hit['seq'] - 1), True
        rows = list(self.db.execute('''SELECT * FROM session_events WHERE session_key=? AND seq>?
            ORDER BY seq LIMIT ?''', (row['key'], cursor, limit + 1)))
        artifacts = {}
        for artifact in self.db.execute('SELECT * FROM artifacts WHERE session_key=?', (row['key'],)):
            artifacts.setdefault(artifact['event_id'], []).append(self.artifact_summary(artifact))
        items = [dict(id=item['id'], seq=item['seq'], source_event_id=item['source_event_id'],
                      parent_event_id=item['parent_event_id'], role=item['role'], text=item['text'],
                      kind=item['kind'], metadata=json.loads(item['metadata']),
                      timestamp=item['timestamp'], source_locator=item['source_locator'],
                      attachments=artifacts.get(item['id'], [])) for item in rows[:limit]]
        return dict(items=items, next_cursor=items[-1]['seq'] if len(rows) > limit else None,
                    generation=revision, reset=False, legacy=False, located=located)

    def scan_sources(self, paths=None):
        roots = self.roots()
        if paths is not None:
            if not isinstance(paths, dict) or any(k not in roots or not isinstance(v, str) for k, v in paths.items()):
                raise ValueError('数据源路径无效')
            with writer_lock(self.db):
                for key, value in paths.items():
                    self.db.execute('INSERT OR REPLACE INTO ui_settings VALUES (?,?)', ('source.' + key, value))
                self.db.commit()
            roots.update({key: Path(value).expanduser() for key, value in paths.items()})
        results = scan(self.db, roots)
        return dict(results=[dict(agent=a, status=s, detail=d) for a, s, d in results], **self.bootstrap())

    def read_artifact(self, artifact_id):
        row = self.db.execute('SELECT * FROM artifacts WHERE id=?', (artifact_id,)).fetchone()
        if row is None or not self.artifact_summary(row)['available']:
            raise ValueError('附件不可读取')
        if row['embedded']:
            data = base64.b64decode(row['embedded'], validate=True)
            mime = row['mime']
        else:
            session = self.db.execute('SELECT cwd FROM sessions WHERE key=?', (row['session_key'],)).fetchone()
            if not session or not session['cwd']:
                raise ValueError('附件缺少工作目录')
            path = Path(row['local_path']).resolve()
            path.relative_to(Path(session['cwd']).resolve())
            with path.open('rb') as stream:
                data = stream.read(MAX_IMAGE_BYTES + 1)
            mime = mimetypes.guess_type(path.name)[0]
        signatures = {'image/png': data.startswith(b'\x89PNG\r\n\x1a\n'),
                      'image/jpeg': data.startswith(b'\xff\xd8\xff'),
                      'image/gif': data.startswith((b'GIF87a', b'GIF89a')),
                      'image/webp': data.startswith(b'RIFF') and data[8:12] == b'WEBP'}
        if len(data) > MAX_IMAGE_BYTES or mime not in IMAGE_TYPES or not signatures.get(mime):
            raise ValueError('附件不是受支持的图片或超过 8 MiB 限制')
        return dict(id=row['id'], name=row['name'], mime=mime,
                    data_url='data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii'))


def response(gateway, request):
    request_id = request.get('id') if isinstance(request, dict) else None
    try:
        if not isinstance(request, dict) or request.get('protocol_version', 1) != PROTOCOL_VERSION:
            raise ValueError('协议版本或请求格式无效')
        result = gateway.dispatch(request.get('method'), request.get('params'))
        return dict(id=request_id, protocol_version=PROTOCOL_VERSION, result=result)
    except (ValueError, TypeError, sqlite3.Error, OSError, RuntimeError) as error:
        return dict(id=request_id, protocol_version=PROTOCOL_VERSION,
                    error=dict(code='request_failed', message=str(error)))


def main():
    parser = argparse.ArgumentParser(description='Harnession JSONL gateway')
    parser.add_argument('--db', type=Path, default=data_dir() / 'index.sqlite3')
    parser.add_argument('--allow-delete', action='store_true')
    args = parser.parse_args()
    gateway = Gateway(args.db, allow_delete=args.allow_delete)
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                result = response(gateway, request)
            except ValueError as error:
                result = dict(id=None, protocol_version=PROTOCOL_VERSION,
                              error=dict(code='invalid_json', message=str(error)))
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        gateway.close()


if __name__ == '__main__':
    main()
