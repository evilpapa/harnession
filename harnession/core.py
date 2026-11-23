import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from collections import defaultdict
from .events import install, parser_version, persist
from .instruction_blocks import starts_with_claude_local_command
from .locking import writer_lock
from .message_parts import (
    codex_events, codex_turn_status, instruction_events, message_events, pi_turn_status, starts_with_turn_status,
)


def data_dir():
    if os.environ.get('HARNESSION_HOME'):
        return Path(os.environ['HARNESSION_HOME']).expanduser()
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'harnession'
    if os.environ.get('XDG_DATA_HOME'):
        return Path(os.environ['XDG_DATA_HOME']) / 'harnession'
    return Path.home() / '.local' / 'share' / 'harnession'


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=30)
    db.row_factory = sqlite3.Row
    with writer_lock(db):
        db.executescript('''
    CREATE TABLE IF NOT EXISTS sessions (
      key TEXT PRIMARY KEY, agent TEXT NOT NULL, source TEXT NOT NULL, native_id TEXT,
      title TEXT, cwd TEXT, project TEXT, created TEXT, updated TEXT, content TEXT,
      fingerprint TEXT NOT NULL, indexed_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS overrides (key TEXT PRIMARY KEY, project TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sources (agent TEXT PRIMARY KEY, path TEXT, status TEXT, detail TEXT, scanned_at TEXT);
    ''')
        install(db)
    return db


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def project_for(cwd):
    if not cwd:
        return None
    p = Path(os.path.abspath(os.path.expanduser(cwd)))
    for parent in (p, *p.parents):
        git = parent / '.git'
        if git.is_dir():
            return str(parent)
        if git.is_file():
            try:
                line = git.read_text(encoding='utf8').strip()
                if line.startswith('gitdir:'):
                    g = Path(line[7:].strip())
                    if not g.is_absolute():
                        g = (parent / g).resolve()
                    if g.parent.name == 'worktrees':
                        return str(g.parent.parent.parent)
            except OSError:
                pass
            return str(parent)
    return str(p)


def text_content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        texts = (text_content(v) for v in value if isinstance(v, (str, dict)))
        return '\n'.join(text for text in texts if text)
    if isinstance(value, dict):
        # Ordinary text only; other blocks become separate typed events.
        if value.get('type') in ('text', 'input_text', 'output_text'):
            return str(value.get('text', ''))
    return ''


def parse_jsonl(path, agent):
    proc = None
    if str(path).endswith('.zst'):
        if not shutil.which('zstd'):
            raise RuntimeError('需要安装 zstd 命令以读取压缩会话')
        proc = subprocess.Popen(['zstd', '-dc', '--', str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf8')
        stream = proc.stdout
    else:
        stream = open(path, encoding='utf8')
    native_id, cwd, created, updated, title = None, None, None, None, None
    session_name = None
    claude_names = {}
    events = []
    tool_names, instructions = {}, {}

    def append_events(batch):
        nonlocal title
        for event in batch:
            if agent == 'codex' and events and events[-1]['seq'] == event['seq'] - 1:
                previous = events[-1]
                before = previous['metadata'].get('turn_status_origin')
                after = event['metadata'].get('turn_status_origin')
                # Codex can write the same interruption twice on consecutive lines.
                if {before, after} == {'marker', 'event'}:
                    marker, structured = (previous, event) if before == 'marker' else (event, previous)
                    marker_status = marker['metadata']['turn_status']
                    structured_status = structured['metadata']['turn_status']
                    if marker_status == structured_status or (
                        marker_status == 'interrupted' and structured_status == 'cancelled'
                        and marker['text'].lstrip().startswith('<turn_aborted>')
                    ):
                        marker['metadata']['turn_status'] = structured_status
                        events[-1] = marker
                        continue
            events.append(event)
            if event['kind'] == 'message' and event['role'] == 'user' and not title:
                title = event['text'].strip()
    try:
        for seq, line in enumerate(stream, 1):
            if agent == 'pi' and not line.strip():
                continue
            try:
                obj = json.loads(line)
            except (ValueError, UnicodeError):
                raise ValueError('JSONL 包含无法解析的记录')
            if not isinstance(obj, dict):
                raise ValueError('JSONL 记录应为对象')
            typ = obj.get('type')
            if agent == 'claude' and typ == 'custom-title':
                session_id, name = obj.get('sessionId'), obj.get('customTitle')
                if isinstance(session_id, str) and isinstance(name, str):
                    claude_names[session_id] = name.strip()
                continue
            stamp = obj.get('timestamp')
            if stamp:
                created = created or str(stamp)
                updated = str(stamp)
            if agent == 'codex':
                payload = obj.get('payload') or {}
                if not isinstance(payload, dict):
                    raise ValueError('Codex payload 应为对象')
                if typ == 'session_meta':
                    native_id = payload.get('id') or payload.get('session_id')
                    cwd = payload.get('cwd')
                    session_name = next((payload[key].strip() for key in ('name', 'thread_name', 'title')
                                         if isinstance(payload.get(key), str) and payload[key].strip()), None)
                if typ in ('session_meta', 'turn_context'):
                    append_events(instruction_events(obj, payload, seq, cwd, instructions))
                if typ == 'response_item':
                    append_events(codex_events(obj, seq, cwd, tool_names))
                elif typ == 'event_msg':
                    append_events(codex_turn_status(obj, seq, cwd))
            elif agent == 'pi':
                if native_id is None:
                    if typ != 'session' or not isinstance(obj.get('id'), str) or not obj['id'].strip():
                        raise ValueError('Pi 会话缺少有效的 session 头部')
                    version = obj.get('version', 1)
                    if type(version) is not int or version not in (1, 2, 3):
                        raise ValueError('不支持的 Pi 会话版本: ' + str(version))
                    native_id = obj['id']
                    cwd = obj.get('cwd')
                    if cwd is not None and not isinstance(cwd, str):
                        raise ValueError('Pi 会话 cwd 应为字符串')
                elif typ == 'session':
                    raise ValueError('Pi 会话包含重复的 session 头部')
                elif typ == 'session_info':
                    name = obj.get('name')
                    if name is not None and not isinstance(name, str):
                        raise ValueError('Pi 会话名称应为字符串')
                    session_name = name.strip() if name else None
                elif typ == 'message':
                    msg = obj.get('message')
                    if not isinstance(msg, dict):
                        raise ValueError('Pi message 记录缺少有效的 message 对象')
                    append_events(message_events(obj, msg, msg.get('role'), seq, cwd))
                    append_events(pi_turn_status(obj, msg, seq, cwd))
            else:
                native_id = obj.get('sessionId') or native_id
                cwd = obj.get('cwd') or cwd
                msg = obj.get('message') or {}
                if typ in ('user', 'assistant', 'system', 'developer', 'tool'):
                    if isinstance(msg, dict):
                        append_events(message_events(obj, msg, msg.get('role') or typ, seq, cwd,
                                                     claude_local_commands=agent == 'claude'))
                    elif isinstance(msg, str):
                        append_events(message_events(obj, {'content': msg}, typ, seq, cwd,
                                                     claude_local_commands=agent == 'claude'))
                    if typ in ('system', 'developer') and not msg:
                        append_events(message_events(obj, {'content': obj.get('content') or obj.get('text')},
                                                     typ, seq, cwd))
    finally:
        stream.close()
        if proc:
            err = proc.stderr.read()
            code = proc.wait()
            if code:
                raise ValueError('zstd 解压失败: ' + err.strip()[:200])
    messages = [(event['role'] if event['kind'] == 'message' else event['kind']) + ': ' + event['text']
                for event in events if event['text']]
    if not native_id and not messages:
        raise ValueError('没有识别到会话记录')
    for event in events:
        metadata = event.get('metadata', {})
        call_id = metadata.get('tool_call_id')
        if event.get('kind') == 'tool_call' and call_id and metadata.get('tool_name'):
            tool_names[call_id] = metadata['tool_name']
        elif event.get('kind') == 'tool_output' and not metadata.get('tool_name'):
            metadata['tool_name'] = tool_names.get(call_id)
    if not title and agent == 'claude' and any(event['metadata'].get('instruction_category') == 'local_command' for event in events):
        title = '本地命令会话'
    if agent == 'claude' and isinstance(native_id, str):
        session_name = claude_names.get(native_id)
    return dict(native_id=native_id or path.stem, cwd=cwd, created=created, updated=updated,
                title=(session_name or title or native_id or path.name).replace('\n', ' ')[:160],
                native_title=session_name, content='\n\n'.join(messages), events=events)


def save(db, agent, source, item, fingerprint):
    key = agent + ':' + source
    db.execute('SAVEPOINT save_session')
    try:
        _save(db, key, agent, source, item, fingerprint)
        db.execute('RELEASE SAVEPOINT save_session')
    except Exception:
        db.execute('ROLLBACK TO SAVEPOINT save_session')
        db.execute('RELEASE SAVEPOINT save_session')
        raise


def _save(db, key, agent, source, item, fingerprint):
    db.execute('''INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
       ON CONFLICT(key) DO UPDATE SET native_id=excluded.native_id,title=excluded.title,
       cwd=excluded.cwd,project=excluded.project,created=excluded.created,updated=excluded.updated,
       content=excluded.content,fingerprint=excluded.fingerprint,indexed_at=excluded.indexed_at''',
       (key, agent, source, str(item.get('native_id') or ''), item.get('title'), item.get('cwd'),
        project_for(item.get('cwd')), item.get('created'), item.get('updated'), item.get('content',''), fingerprint, now()))
    if 'events' in item:
        persist(db, key, item['events'], parser_version(agent))
    else:
        from .events import stable_id
        db.execute('INSERT OR IGNORE INTO event_state VALUES (?,?,0)', (key, stable_id(key)))


def file_fingerprint(stat, agent):
    fingerprint = str(stat.st_mtime_ns) + ':' + str(stat.st_size)
    if agent in ('codex', 'claude'):
        return f'{fingerprint}:{agent}-titles-v1'
    return fingerprint


def scan_files(db, agent, root):
    count = skipped = errors = 0
    messages = []
    codex_titles = {}
    unchanged_keys = set()
    if agent == 'codex':
        from .codex_titles import load_codex_titles, refresh_codex_titles
        codex_titles = load_codex_titles(root)
    for path in sorted(root.rglob('*')):
        if not path.is_file() or not (path.name.endswith('.jsonl') or (agent == 'codex' and path.name.endswith('.jsonl.zst'))):
            continue
        if agent == 'codex' and path.name == 'session_index.jsonl':
            continue
        try:
            fingerprint = file_fingerprint(path.stat(), agent)
            source = str(path.resolve())
            existing = db.execute('''SELECT s.fingerprint,e.parser_version FROM sessions s
                LEFT JOIN event_state e ON e.session_key=s.key WHERE s.key=?''', (agent + ':' + source,)).fetchone()
            if existing and existing[0] == fingerprint and existing[1] == parser_version(agent):
                skipped += 1
                unchanged_keys.add(agent + ':' + source)
                continue
            item = parse_jsonl(path, agent)
            if agent == 'codex' and codex_titles.get(item['native_id']):
                item['title'] = codex_titles[item['native_id']]
            save(db, agent, source, item, fingerprint)
            count += 1
        except (OSError, ValueError, RuntimeError) as e:
            errors += 1
            if len(messages) < 5:
                messages.append(path.name + ': ' + str(e))
    if agent == 'codex':
        renamed = refresh_codex_titles(db, root, codex_titles)
        count += len(renamed)
        skipped -= len(renamed & unchanged_keys)
    return count, skipped, errors, messages


def scan_hermes(db, path, session_ids=None):
    # mode=ro prevents modifications to the source database.
    uri = path.resolve().as_uri() + '?mode=ro'
    src = sqlite3.connect(uri, uri=True)
    src.row_factory = sqlite3.Row
    count = 0
    try:
        def decoded(value):
            if isinstance(value, str) and value.lstrip().startswith(('[', '{')):
                try:
                    return json.loads(value)
                except ValueError:
                    pass
            return value

        def timestamp(x):
            if isinstance(x, (int, float)):
                try: return dt.datetime.fromtimestamp(x, dt.timezone.utc).isoformat()
                except (OverflowError, ValueError, OSError): return str(x)
            return str(x) if x is not None else None

        tables = {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for s in src.execute('SELECT * FROM sessions'):
            if session_ids is not None and str(s['id']) not in session_ids:
                continue
            texts, events = [], []
            title = None
            system_prompt = s['system_prompt'] if 'system_prompt' in s.keys() else None
            if 'system_prompt_hash' in s.keys() and s['system_prompt_hash'] and 'system_prompts' in tables:
                prompt = src.execute('SELECT prompt FROM system_prompts WHERE hash=?', (s['system_prompt_hash'],)).fetchone()
                system_prompt = prompt[0] if prompt else system_prompt
            if system_prompt:
                batch = message_events({'id': 'session-system:' + str(s['id'])},
                                       {'content': decoded(system_prompt)}, 'system', 0, s['cwd'])
                for event in batch:
                    event['source_locator'] = 'sessions.id:' + str(s['id']) + '#system_prompt'
                events.extend(batch)
            tool_names = {}
            for m in src.execute('SELECT * FROM messages WHERE session_id=? ORDER BY id', (s['id'],)):
                message = {key: decoded(m[key]) for key in m.keys()
                           if key in ('content', 'tool_calls', 'tool_call_id', 'tool_name', 'reasoning', 'reasoning_content')}
                record = {'id': m['id'], 'timestamp': timestamp(m['timestamp']) if 'timestamp' in m.keys() else None}
                batch = message_events(record, message, m['role'], len(events) + 1, s['cwd'])
                for event in batch:
                    event['source_locator'] = 'messages.id:' + str(m['id']) + (
                        '#part:' + event['identity_suffix'] if event.get('identity_suffix') else '')
                    call_id = event['metadata'].get('tool_call_id')
                    if event['kind'] == 'tool_call' and call_id and event['metadata'].get('tool_name'):
                        tool_names[call_id] = event['metadata']['tool_name']
                    elif event['kind'] == 'tool_output' and not event['metadata'].get('tool_name'):
                        event['metadata']['tool_name'] = tool_names.get(call_id)
                    events.append(event)
                    if event['kind'] == 'message' and event['role'] == 'user' and not title:
                        title = event['text']
            for event in events:
                if event['text']:
                    label = event['role'] if event['kind'] == 'message' else event['kind']
                    texts.append(label + ': ' + event['text'])
            source = str(path.resolve()) + '#' + str(s['id'])
            item = dict(native_id=s['id'], cwd=s['cwd'], created=timestamp(s['started_at']),
                        updated=timestamp(s['last_activity_at']), title=s['title'] or (title[:160] if title else str(s['id'])), content='\n\n'.join(texts), events=events)
            if session_ids is not None and not events:
                continue
            save(db, 'hermes', source, item, str(s['last_activity_at']))
            count += 1
    finally:
        src.close()
    return count, 0, 0, []


def refresh_stale_events(db):
    """Reparse indexed sources only, preserving snapshots that cannot be upgraded."""
    stale = db.execute('''SELECT s.key,s.agent,s.source,s.native_id,s.title FROM sessions s
        JOIN event_state e ON e.session_key=s.key WHERE s.agent IN ('codex','claude','pi','hermes')
        AND e.parser_version < CASE WHEN s.agent='claude' THEN ? ELSE ? END''',
                       (parser_version('claude'), parser_version('codex'))).fetchall()
    hermes = defaultdict(set)
    for row in stale:
        agent, source = row['agent'], row['source']
        if not source or row['key'] != agent + ':' + source:
            continue
        if agent == 'hermes':
            native_id = row['native_id']
            if native_id and source.endswith('#' + native_id):
                path = Path(source[:-(len(native_id) + 1)])
                # The Hermes reader reconstructs its key from the canonical DB path.
                if path.is_absolute():
                    hermes[path].add(native_id)
            continue
        path = Path(source)
        if not path.is_absolute():
            continue
        try:
            if not path.is_file() or path.resolve() != path:
                continue
            fingerprint = file_fingerprint(path.stat(), agent)
            item = parse_jsonl(path, agent)
        except (OSError, ValueError, RuntimeError):
            continue
        has_claude_title = agent == 'claude' and item['native_title'] is not None
        if (not item['events'] and not has_claude_title) or (row['native_id'] and row['native_id'] != str(item['native_id'])):
            continue
        if not has_claude_title and row['title'] and not starts_with_turn_status(row['title'], agent) and not (
            agent == 'claude' and starts_with_claude_local_command(row['title'])
        ):
            item['title'] = row['title']
        save(db, agent, source, item, fingerprint)
    for path, session_ids in hermes.items():
        try:
            if path.is_file() and path.resolve() == path:
                scan_hermes(db, path, session_ids)
        except (OSError, ValueError, sqlite3.Error):
            continue


def scan(db, roots):
    with writer_lock(db):
        return _scan(db, roots)


def _scan(db, roots):
    results = []
    for agent in ('codex', 'claude', 'hermes', 'pi', 'opencode'):
        path = roots[agent]
        if agent == 'opencode':
            status, detail = 'unsupported', '适配器尚未实现'
        elif not path.exists():
            status, detail = 'missing', '未找到数据源'
        elif agent == 'hermes' and not path.is_file():
            status, detail = 'error', '数据源应为 SQLite 文件'
        elif agent in ('codex', 'claude', 'pi') and not path.is_dir():
            status, detail = 'error', '数据源应为目录'
        else:
            try:
                count, skipped, errors, messages = scan_hermes(db, path) if agent == 'hermes' else scan_files(db, agent, path)
                status = 'partial' if errors else 'ok'
                detail = f'更新 {count}，未变化 {skipped}，失败 {errors}' + ('；' + '；'.join(messages) if messages else '')
            except (OSError, sqlite3.Error, ValueError) as e:
                status, detail = 'error', str(e)
        db.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?)', (agent, str(path), status, detail, now()))
        db.commit()
        results.append((agent, status, detail))
    return results


def defaults():
    home = Path.home()
    codex = Path(os.environ.get('CODEX_HOME', str(home / '.codex')))
    claude = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(home / '.claude')))
    hermes = Path(os.environ.get('HERMES_HOME', str(home / '.hermes')))
    pi = Path(os.environ.get('PI_CODING_AGENT_DIR') or str(home / '.pi' / 'agent')).expanduser()
    pi_sessions = Path(os.environ.get('PI_CODING_AGENT_SESSION_DIR') or str(pi / 'sessions')).expanduser()
    return {'codex': codex / 'sessions', 'claude': claude / 'projects', 'hermes': hermes / 'state.db',
            'pi': pi_sessions, 'opencode': Path(os.environ.get('XDG_DATA_HOME', str(home / '.local' / 'share'))) / 'opencode'}
