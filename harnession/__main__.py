import argparse
from pathlib import Path
import sys
from .core import connect, data_dir, defaults, scan
from .locking import writer_lock


CONFIGURABLE_SOURCES = ('codex', 'claude', 'hermes', 'pi', 'antigravity')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='harnession', description='本地 AI Agent 会话索引')
    parser.add_argument('--db', type=Path, default=data_dir() / 'index.sqlite3', help='索引数据库路径')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('scan', help='扫描本机会话')
    for agent in CONFIGURABLE_SOURCES:
        p.add_argument('--' + agent, type=Path, help='覆盖数据源路径')
    sub.add_parser('status', help='查看数据源状态')
    sub.add_parser('projects', help='查看项目')
    p = sub.add_parser('list', help='列出会话')
    p.add_argument('--agent', choices=(*CONFIGURABLE_SOURCES, 'opencode'))
    p.add_argument('--project', help='项目路径的子串')
    p.add_argument('--search', help='标题或会话内容的子串')
    p.add_argument('--limit', type=int, default=50)
    p = sub.add_parser('show', help='查看会话详情')
    p.add_argument('id', type=int)
    p = sub.add_parser('assign', help='手动设置项目路径')
    p.add_argument('id', type=int)
    p.add_argument('project', help='项目路径；使用 --reset 恢复自动归类', nargs='?')
    p.add_argument('--reset', action='store_true')
    args = parser.parse_args(argv)
    db = connect(args.db)
    try:
        if args.command == 'scan':
            roots = defaults()
            for name in CONFIGURABLE_SOURCES:
                if getattr(args, name): roots[name] = getattr(args, name).expanduser()
            results = scan(db, roots)
            for agent, status, detail in results: print(f'{agent}: {status} — {detail}')
            return 1 if any(status in ('error', 'partial') for _, status, _ in results) else 0
        if args.command == 'status':
            rows = db.execute('SELECT * FROM sources ORDER BY agent').fetchall()
            if not rows: print('尚未扫描，请先运行 scan。')
            for row in rows: print(f"{row['agent']}: {row['status']} — {row['detail']} ({row['path']})")
        elif args.command == 'projects':
            rows = db.execute('''SELECT COALESCE(o.project,s.project,'未归类') AS p, count(*) AS n FROM sessions s LEFT JOIN overrides o ON o.key=s.key GROUP BY p ORDER BY n DESC,p''')
            for row in rows: print(f"{row['n']:>5}  {row['p']}")
        elif args.command == 'list':
            if args.limit < 1: parser.error('--limit 必须大于 0')
            sql = '''SELECT s.rowid AS id,s.*,COALESCE(o.project,s.project) AS effective_project FROM sessions s LEFT JOIN overrides o ON o.key=s.key WHERE 1=1'''
            params = []
            if args.agent: sql += ' AND s.agent=?'; params.append(args.agent)
            if args.project: sql += " AND instr(COALESCE(o.project,s.project,''),?)>0"; params.append(args.project)
            if args.search: sql += " AND (instr(COALESCE(s.title,''),?)>0 OR instr(COALESCE(s.content,''),?)>0)"; params.extend([args.search,args.search])
            sql += ' ORDER BY COALESCE(s.updated,s.created,s.indexed_at) DESC LIMIT ?'; params.append(args.limit)
            for row in db.execute(sql, params):
                print(f"{row['id']:>5}  {row['agent']:<7}  {(row['updated'] or row['created'] or '?')[:19]:<19}  {row['effective_project'] or '未归类'}\n       {row['title'] or '无标题'}")
        elif args.command == 'show':
            row = db.execute('''SELECT s.*,COALESCE(o.project,s.project) AS effective_project FROM sessions s LEFT JOIN overrides o ON o.key=s.key WHERE s.rowid=?''',(args.id,)).fetchone()
            if not row: print('会话不存在', file=sys.stderr); return 1
            print(f"{row['title']}\nAgent: {row['agent']}\n项目: {row['effective_project'] or '未归类'}\n工作目录: {row['cwd'] or '未知'}\n来源: {row['source']}\n原始 ID: {row['native_id']}\n\n{row['content']}")
        elif args.command == 'assign':
            row = db.execute('SELECT key FROM sessions WHERE rowid=?',(args.id,)).fetchone()
            if not row: print('会话不存在', file=sys.stderr); return 1
            with writer_lock(db):
                if args.reset: db.execute('DELETE FROM overrides WHERE key=?',(row['key'],))
                elif args.project: db.execute('INSERT OR REPLACE INTO overrides VALUES (?,?)',(row['key'],str(Path(args.project).expanduser().absolute())))
                else: parser.error('请提供项目路径或 --reset')
                db.commit()
            print('项目归属已更新')
        return 0
    finally:
        db.close()

if __name__ == '__main__':
    sys.exit(main())
