"""Read Antigravity desktop and CLI conversation stores."""

from contextlib import closing, contextmanager
import datetime as dt
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from urllib.request import url2pathname
from uuid import UUID


TRANSCRIPTS = ('transcript_full.jsonl', 'transcript.jsonl')
TRANSCRIPT_ROLES = {'USER_INPUT': 'user', 'PLANNER_RESPONSE': 'assistant'}
CONTEXT_SUFFIX = re.compile(r'^\s*<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>\s*'
                            r'(?:<ADDITIONAL_METADATA>|<USER_SETTINGS_CHANGE>|$)', re.DOTALL)


def profiles(root):
    if any((root / name).exists() for name in ('conversations', 'brain', 'conversation_summaries.db')):
        return [root]
    return [path for name in ('antigravity', 'antigravity-cli') if (path := root / name).is_dir()]


def candidates(profile):
    conversations = profile / 'conversations'
    if conversations.exists():
        for path in sorted(conversations.iterdir()):
            if path.suffix in ('.db', '.pb') and path.is_file() and is_conversation_id(path.stem):
                yield path.stem, path
    brain = profile / 'brain'
    if brain.exists():
        for session in sorted(brain.iterdir()):
            if not session.is_dir() or not is_conversation_id(session.name):
                continue
            logs = session / '.system_generated' / 'logs'
            for name in TRANSCRIPTS:
                path = logs / name
                if path.is_file():
                    yield session.name, path
                    break


def is_conversation_id(value):
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def signature(path):
    stat = path.stat()
    wal = None
    if path.suffix == '.db':
        try:
            sidecar = path.with_name(path.name + '-wal').stat()
        except FileNotFoundError:
            pass
        else:
            if sidecar.st_size:
                wal = (sidecar.st_ino, sidecar.st_size, sidecar.st_mtime_ns)
    return stat.st_mtime_ns, stat.st_size, stat.st_ino, wal


@contextmanager
def read_database(path, before):
    uri = path.resolve().as_uri() + '?mode=ro'
    if before[-1] is None:
        uri += '&immutable=1'
    elif not path.with_name(path.name + '-shm').is_file():
        raise ValueError('SQLite 正在使用 WAL，缺少共享内存文件，请重新扫描')
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
        db.execute('BEGIN')
        yield db
    if signature(path) != before:
        raise ValueError('读取期间会话库已更新，请重新扫描')


def timestamp(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r'(\.\d{6})\d+', r'\1', value.replace('Z', '+00:00'))
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.year == 1:
        return None
    zone = parsed.tzinfo or dt.timezone.utc
    return parsed.replace(tzinfo=zone).astimezone(dt.timezone.utc).isoformat()


def workspace_path(value):
    uris = json.loads(value or '[]')
    if not isinstance(uris, list):
        raise ValueError('Antigravity 工作区 URI 应为数组')
    for uri in uris:
        if not isinstance(uri, str):
            continue
        parsed = urlsplit(uri)
        if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost'):
            continue
        path = Path(url2pathname(parsed.path))
        if path.is_absolute():
            return str(path)
    return None


def summaries(profile):
    path = profile / 'conversation_summaries.db'
    if not path.is_file():
        return {}
    with read_database(path, signature(path)) as db:
        return {
            native_id: (title, workspace_path(uris), timestamp(updated))
            for native_id, title, uris, updated in db.execute(
                'SELECT conversation_id,title,workspace_uris,last_modified_time FROM conversation_summaries')
        }


def varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            break
        byte = data[offset]
        offset += 1
        if shift == 63 and byte > 1:
            break
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError('Antigravity Protobuf 记录已损坏')


def protobuf_fields(data, wanted):
    if not isinstance(data, bytes):
        raise ValueError('Antigravity Protobuf 字段应为二进制')
    fields, offset = {}, 0
    while offset < len(data):
        tag, offset = varint(data, offset)
        number, wire = divmod(tag, 8)
        if not number:
            raise ValueError('Antigravity Protobuf 字段编号无效')
        if wire == 0:
            value, offset = varint(data, offset)
        elif wire == 2:
            size, offset = varint(data, offset)
            end = offset + size
            if end > len(data):
                raise ValueError('Antigravity Protobuf 字段不完整')
            value = data[offset:end] if number in wanted else None
            offset = end
        elif wire in (1, 5):
            value = None
            offset += 8 if wire == 1 else 4
        else:
            raise ValueError('Antigravity Protobuf 字段类型不受支持')
        if offset > len(data):
            raise ValueError('Antigravity Protobuf 字段不完整')
        if number in wanted:
            fields[number] = value
    return fields


def step_timestamp(metadata):
    if not metadata:
        return None
    fields = protobuf_fields(protobuf_fields(metadata, {1}).get(1, b''), {1, 2})
    seconds, nanoseconds = fields.get(1), fields.get(2, 0)
    if type(seconds) is not int or type(nanoseconds) is not int or not 0 <= nanoseconds < 10**9:
        return None
    try:
        return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).replace(microsecond=nanoseconds // 1000).isoformat()
    except (OverflowError, ValueError, OSError):
        return None


def database_messages(path, native_id, before):
    with read_database(path, before) as db:
        if db.execute('SELECT cascade_id FROM trajectory_meta').fetchone() != (native_id,):
            raise ValueError('Antigravity 数据库会话 ID 与文件名不一致')
        for index, step_type, step_format, metadata, payload in db.execute(
            'SELECT idx,step_type,step_format,metadata,step_payload FROM steps WHERE step_type IN (14,15) ORDER BY idx'
        ):
            if step_format != 0:
                raise ValueError('Antigravity 会话步骤格式不受支持: ' + str(step_format))
            user = step_type == 14
            role, field, text_field = ('user', 19, 2) if user else ('assistant', 20, 1)
            wrapper = protobuf_fields(payload, {1, field})
            if wrapper.get(1) != step_type or field not in wrapper:
                raise ValueError('Antigravity 会话步骤缺少消息载荷')
            text = protobuf_fields(wrapper[field], {text_field}).get(text_field, b'')
            if not isinstance(text, bytes):
                raise ValueError('Antigravity 消息字段应为文本')
            yield index, role, text.decode('utf8').strip(), step_timestamp(metadata), 'steps.idx:' + str(index)


def transcript_messages(path, before):
    with path.open(encoding='utf8') as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get('type'), str):
                raise ValueError('Antigravity 转录记录无效')
            role = TRANSCRIPT_ROLES.get(row['type'])
            if not role:
                continue
            index, text = row.get('step_index'), row.get('content', '')
            if type(index) is not int or not isinstance(text, str):
                raise ValueError('Antigravity 转录消息格式无效')
            if role == 'user' and (request := CONTEXT_SUFFIX.match(text)):
                text = request[1]
            yield index, role, text.strip(), timestamp(row.get('created_at')), 'line:' + str(line_number)
    if signature(path) != before:
        raise ValueError('读取期间转录文件已更新，请重新扫描')


def parse_conversation(path, native_id, summary, before):
    title, cwd, updated = summary
    created = None
    events = []
    messages = database_messages(path, native_id, before) if path.suffix == '.db' else transcript_messages(path, before)
    for index, role, text, stamp, locator in messages:
        if stamp:
            created = min(created or stamp, stamp)
            updated = max(updated or stamp, stamp)
        if text:
            events.append(dict(source_event_id=str(index), parent_event_id=None, role=role,
                               kind='message', timestamp=stamp, text=text, source_locator=locator))
    if not events:
        return None
    first_question = next((event['text'] for event in events if event['role'] == 'user'), None)
    return dict(native_id=native_id, cwd=cwd, created=created, updated=updated,
                title=' '.join(((title or '').strip() or first_question or native_id).splitlines())[:160],
                content='\n\n'.join(event['role'] + ': ' + event['text'] for event in events), events=events)
