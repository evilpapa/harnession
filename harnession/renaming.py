"""Write session names to the original Agent's local storage."""

from contextlib import closing, ExitStack
import errno
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep
import unicodedata
from uuid import uuid4

from .codex_titles import codex_home, codex_state_path
from .core import now
from .deletion import session_source


_HERMES_INVISIBLE = re.compile(r'[\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff\ufffc\ufff9-\ufffb]')


def title_limit(agent):
    return 100 if agent == 'hermes' else 160


def validated_title(agent, title):
    if not isinstance(title, str):
        raise ValueError('会话标题必须为文本。')
    if any(unicodedata.category(char) in ('Cc', 'Cs', 'Zl', 'Zp') for char in title):
        raise ValueError('会话标题必须为单行文本。')
    if agent == 'hermes':
        title = ' '.join(_HERMES_INVISIBLE.sub('', title).split())
    else:
        title = title.strip()
    limit = title_limit(agent)
    if not 1 <= len(title) <= limit:
        raise ValueError(f'会话标题必须为 1–{limit} 个字符。')
    return title


def rename_target(agent, native_id, source, root):
    path, _, reason = session_source(agent, native_id, source, root)
    if reason:
        raise ValueError(reason)
    if agent == 'hermes':
        if path != Path(source.rsplit('#', 1)[0]):
            raise ValueError('原始数据库路径已改变。')
        command = shutil.which('hermes')
        if not command or (os.name == 'nt' and Path(command).suffix.lower() in ('.cmd', '.bat')):
            raise ValueError('本机需要支持 sessions rename 的 Hermes CLI。')
        return path, command
    if path != Path(source) or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError('原始会话文件已改变。')
    if agent == 'codex':
        if not path.name.endswith(('.jsonl', '.jsonl.zst')) or path.name == 'session_index.jsonl':
            raise ValueError('此文件不是 Codex 会话记录。')
        command = shutil.which('codex')
        if not command:
            raise ValueError('本机需要支持会话改名的 Codex CLI。')
        return path, command
    if not path.name.endswith('.jsonl') or (agent == 'claude' and path.stem != native_id):
        raise ValueError('此文件不是可改名的主会话记录。')
    return path, None


def rename_native(agent, native_id, path, command, title):
    if agent == 'codex':
        _rename_codex(command, path, native_id, title)
    elif agent == 'hermes':
        _rename_hermes(command, path, native_id, title)
    else:
        return _append_title(path, agent, native_id, title)


def _native_env(**overrides):
    env = dict(os.environ, **overrides)
    # A bundled Python gateway must not load its own shared libraries into an Agent.
    if getattr(sys, 'frozen', False):
        original = env.pop('LD_LIBRARY_PATH_ORIG', None)
        if original is None:
            env.pop('LD_LIBRARY_PATH', None)
        else:
            env['LD_LIBRARY_PATH'] = original
    return env


def _rename_codex(command, path, native_id, title):
    home = codex_home(path.parent)
    env = _native_env(CODEX_HOME=str(home))
    # Honor a separate state directory for the primary profile only.
    primary = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser().resolve()
    if home != primary:
        env['CODEX_SQLITE_HOME'] = str(home)
    with subprocess.Popen([command, 'app-server', '--listen', 'stdio://'], cwd=home, env=env,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, encoding='utf8', errors='replace') as proc:
        replies = Queue()

        def collect():
            for line in proc.stdout:
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue
                if isinstance(reply, dict) and type(reply.get('id')) is int:
                    replies.put(reply)
            replies.put(None)

        def send(message):
            proc.stdin.write(json.dumps(message) + '\n')
            proc.stdin.flush()

        def call(request_id, method, params):
            send(dict(id=request_id, method=method, params=params))
            while True:
                reply = replies.get(timeout=max(0, deadline - monotonic()))
                if reply is None:
                    raise ValueError('Codex 改名服务已退出。')
                if reply['id'] == request_id:
                    if 'result' not in reply:
                        error = reply.get('error')
                        reason = error.get('message', '') if isinstance(error, dict) else ''
                        raise ValueError(f'Codex 无法同步会话标题。{str(reason)[:240]}')
                    return reply['result']

        def read_thread(request_id, query):
            result = call(request_id, 'thread/read', query)
            thread = result.get('thread') if isinstance(result, dict) else None
            if not isinstance(thread, dict):
                raise ValueError('当前 Codex 版本未返回有效的会话信息。')
            return thread

        reader = Thread(target=collect, daemon=True)
        reader.start()
        deadline = monotonic() + 12
        try:
            call(1, 'initialize', {'clientInfo': {'name': 'harnession', 'version': '0.1.0'}})
            send({'method': 'initialized'})
            query = {'threadId': native_id, 'includeTurns': False}
            thread = read_thread(2, query)
            # Profiles may contain both archived and active copies of a session ID.
            native_path = Path(thread['path']).resolve() if isinstance(thread.get('path'), str) else None
            # Codex reports the logical .jsonl path for compressed archives.
            if (native_path is not None and path.suffix == '.zst' and native_path == path.with_suffix('')
                    and not native_path.exists()):
                native_path = path
            if thread.get('id') != native_id or native_path != path:
                raise ValueError('Codex 未能通过原始 ID 定位到此会话文件。')
            if (home / 'archived_sessions') in path.parents and thread.get('historyMode', 'legacy') == 'legacy':
                state_home = Path(env.get('CODEX_SQLITE_HOME') or home).expanduser()
                _rename_codex_archive(home, state_home, path, native_id, title)
            else:
                call(3, 'thread/name/set', {'threadId': native_id, 'name': title})
            if read_thread(4, query).get('name') != title:
                raise ValueError('无法确认 Codex 已保存标题，请重新打开会话后重试。')
        except Empty as error:
            raise ValueError('等待 Codex 响应超时，请重新打开会话后重试。') from error
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            reader.join(timeout=1)


def _rename_codex_archive(home, state_home, path, native_id, title):
    """The Codex API cannot set names on archived legacy rollouts."""
    with ExitStack() as stack:
        lock = stack.enter_context(_open_file(home / 'session_index.jsonl.lock', os.O_RDWR | os.O_CREAT))
        deadline = monotonic() + 3
        while not _try_codex_lock(lock.fileno()):
            if monotonic() >= deadline:
                raise ValueError('Codex 正在写入会话索引，请重试。')
            sleep(0.05)

        state = codex_state_path(state_home)
        if state:
            if state.is_symlink():
                raise ValueError('Codex 状态数据库路径已改变。')
            db = stack.enter_context(closing(sqlite3.connect(state.as_uri() + '?mode=rw', uri=True, timeout=1)))
            stack.enter_context(db)
            db.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in db.execute('PRAGMA table_info(threads)')}
            if {'id', 'title', 'archived', 'rollout_path'} <= columns:
                row = db.execute('SELECT archived,rollout_path FROM threads WHERE id=?', (native_id,)).fetchone()
                if row:
                    paths = {str(path)}
                    if path.suffix == '.zst':
                        paths.add(str(path.with_suffix('')))
                    if not row[0] or row[1] not in paths:
                        raise ValueError('Codex 中的归档会话位置已改变。')
                    if 'name' in columns:
                        db.execute('UPDATE threads SET title=?,name=CASE WHEN name IS NULL THEN NULL ELSE ? END WHERE id=?',
                                   (title, title, native_id))
                    else:
                        db.execute('UPDATE threads SET title=? WHERE id=?', (title, native_id))
        if not path.is_file():
            raise ValueError('Codex 归档会话已移动或不存在。')
        index = stack.enter_context(_open_file(home / 'session_index.jsonl', os.O_RDWR | os.O_APPEND | os.O_CREAT))
        _write_record(index, dict(id=native_id, thread_name=title, updated_at=now()))


def _try_codex_lock(descriptor):
    # Codex uses std::fs::File::try_lock on session_index.jsonl.lock.
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return False
        raise


def _rename_hermes(command, path, native_id, title):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        # Hermes accepts ID prefixes; verify that the full ID identifies an existing row.
        if db.execute('SELECT 1 FROM sessions WHERE id=?', (native_id,)).fetchone() is None:
            raise ValueError('Hermes 中的原始会话已不存在。')
        try:
            result = subprocess.run([command, 'sessions', 'rename', '--', native_id, title],
                                    cwd=path.parent, env=_native_env(HERMES_HOME=str(path.parent),
                                                                    HERMES_GATEWAY_PROFILE_PIN='1'),
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    encoding='utf8', errors='replace', timeout=12)
        except subprocess.TimeoutExpired as error:
            raise ValueError('等待 Hermes 响应超时，请重新打开会话后重试。') from error
        if result.returncode:
            lines = (result.stdout or result.stderr).strip().splitlines()
            raise ValueError('Hermes 无法同步会话标题。' + (lines[-1][:240] if lines else ''))
        renamed = db.execute('SELECT title FROM sessions WHERE id=?', (native_id,)).fetchone()
        if not renamed or renamed[0] != title:
            raise ValueError('无法确认 Hermes 已保存标题，请重新打开会话后重试。')


def _open_file(path, flags):
    flags |= getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    if path.is_symlink():
        raise ValueError('原始会话路径已变为符号链接。')
    stream = os.fdopen(os.open(path, flags, 0o600), 'rb')
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        stream.close()
        raise ValueError('来源必须是单个独立的普通文件。')
    return stream


def _write_record(stream, record):
    separator = b''
    if stream.seek(0, os.SEEK_END):
        stream.seek(-1, os.SEEK_END)
        if stream.read(1) != b'\n':
            separator = b'\n'
    data = separator + (json.dumps(record, ensure_ascii=False) + '\n').encode('utf8')
    if os.write(stream.fileno(), data) != len(data):
        raise OSError('无法完整写入原始会话标题。')
    os.fsync(stream.fileno())
    return len(data)


def _append_title(path, agent, native_id, title):
    with _open_file(path, os.O_RDWR | os.O_APPEND) as stream:
        descriptor = stream.fileno()
        original = os.fstat(descriptor)
        if agent == 'pi':
            header = json.loads(next((line for line in stream if line.strip()), b'{}'))
            if not isinstance(header, dict) or header.get('type') != 'session' or header.get('id') != native_id:
                raise ValueError('Pi 文件中的原始会话 ID 已改变。')
            record = _pi_title_record(stream, header, title)
        else:
            matched = False
            for line in stream:
                entry = json.loads(line)
                if isinstance(entry, dict) and entry.get('type') in ('user', 'assistant', 'system', 'developer', 'tool'):
                    other_id = entry.get('sessionId')
                    if other_id and other_id != native_id:
                        raise ValueError('Claude Code 文件中的原始会话 ID 已改变。')
                    matched |= other_id == native_id
            if not matched:
                raise ValueError('Claude Code 文件中找不到原始会话 ID。')
            record = dict(type='custom-title', customTitle=title, sessionId=native_id)
        current = path.lstat()
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if any(getattr(original, field) != getattr(current, field) for field in fields):
            raise ValueError('原始会话文件正在变化，请重试。')
        size = _write_record(stream, record)
        updated = os.fstat(descriptor)
        if updated.st_size == original.st_size + size:
            return original, updated


def _pi_title_record(stream, header, title):
    version = header.get('version', 1)
    if type(version) is not int or version not in (1, 2, 3):
        raise ValueError('不支持此 Pi 会话格式。')
    ids = set()
    leaf = None
    for line in stream:
        if not line.strip():
            continue
        entry = json.loads(line)
        if not isinstance(entry, dict) or entry.get('type') == 'session':
            raise ValueError('Pi 会话含有无效记录。')
        leaf = entry.get('id')
        if version > 1 and not isinstance(leaf, str):
            raise ValueError('Pi 会话的分支记录缺少有效 ID。')
        if isinstance(leaf, str):
            ids.add(leaf)
    entry_id = uuid4().hex[:8]
    while entry_id in ids:
        entry_id = uuid4().hex[:8]
    return dict(type='session_info', name=title, id=entry_id,
                parentId=leaf if version > 1 else None, timestamp=now())
