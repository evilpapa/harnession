"""Versioned message snapshots. Indexing never modifies source files."""

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlparse

PARSER_VERSION = 5
CLAUDE_PARSER_VERSION = 6
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}


def parser_version(agent):
    return CLAUDE_PARSER_VERSION if agent == 'claude' else PARSER_VERSION


def stable_id(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode('utf8')).hexdigest()[:32]


def install(db):
    # Additive migration: the legacy sessions table and its rowids stay intact.
    db.executescript('''
    CREATE TABLE IF NOT EXISTS event_state (
      session_key TEXT PRIMARY KEY, session_id TEXT UNIQUE NOT NULL,
      parser_version INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS session_events (
      id TEXT PRIMARY KEY, session_key TEXT NOT NULL, seq INTEGER NOT NULL,
      source_event_id TEXT, parent_event_id TEXT, role TEXT NOT NULL,
      timestamp TEXT, text TEXT NOT NULL, source_locator TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS events_session_seq ON session_events(session_key,seq);
    CREATE TABLE IF NOT EXISTS artifacts (
      id TEXT PRIMARY KEY, session_key TEXT NOT NULL, event_id TEXT NOT NULL,
      mime TEXT, name TEXT NOT NULL, local_path TEXT, embedded TEXT, reason TEXT);
    CREATE INDEX IF NOT EXISTS artifacts_session ON artifacts(session_key,event_id);
    CREATE TABLE IF NOT EXISTS ui_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    columns = {row[1] for row in db.execute('PRAGMA table_info(session_events)')}
    if 'kind' not in columns:
        db.execute("ALTER TABLE session_events ADD COLUMN kind TEXT NOT NULL DEFAULT 'message'")
    if 'metadata' not in columns:
        db.execute("ALTER TABLE session_events ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
    for row in db.execute('SELECT key FROM sessions WHERE key NOT IN (SELECT session_key FROM event_state)'):
        db.execute('INSERT OR IGNORE INTO event_state VALUES (?,?,0)', (row['key'], stable_id(row['key'])))
    db.commit()


def image_parts(content, cwd):
    """Only explicit image blocks; never guess files from a directory listing."""
    images = []
    if not isinstance(content, list):
        return images
    for block in content:
        if not isinstance(block, dict) or block.get('type') not in ('image', 'input_image', 'image_url'):
            continue
        image = dict(mime=None, name='图片附件', local_path=None, embedded=None, reason=None)
        source = block.get('source')
        if isinstance(source, dict) and source.get('type') == 'base64':
            image['mime'], image['embedded'] = source.get('media_type'), source.get('data')
        elif block.get('type') == 'image' and block.get('data'):
            image['mime'], image['embedded'] = block.get('mimeType'), block.get('data')
        else:
            url = block.get('image_url') or block.get('url') or ''
            if isinstance(url, dict):
                url = url.get('url', '')
            if isinstance(url, str) and url.startswith('data:') and ';base64,' in url:
                image['mime'], image['embedded'] = url[5:].split(';base64,', 1)
            elif isinstance(url, str) and url.startswith('file:') and cwd:
                root = Path(cwd).expanduser().resolve()
                candidate = Path(unquote(urlparse(url).path)).resolve()
                try:
                    candidate.relative_to(root)
                    image['local_path'] = str(candidate)
                    image['name'] = candidate.name
                except ValueError:
                    image['reason'] = '附件不在此会话工作目录内'
            else:
                image['reason'] = '未提供可本地读取的图片；远程图片不会自动下载'
        if image['embedded'] is not None:
            value = image['embedded']
            if image['mime'] not in IMAGE_TYPES or not isinstance(value, str) or len(value) > MAX_IMAGE_BYTES * 4 // 3 + 4:
                image['embedded'], image['reason'] = None, '图片类型不支持或超过 8 MiB 限制'
            else:
                try:
                    base64.b64decode(value, validate=True)
                except ValueError:
                    image['embedded'], image['reason'] = None, '图片编码损坏'
        if not any((image['local_path'], image['embedded'], image['reason'])):
            image['reason'] = '来源未提供可读取图片'
        images.append(image)
    return images


def visible_event(obj, role, text, content, seq, cwd):
    native_id = obj.get('uuid') or obj.get('id')
    parent_id = obj.get('parentUuid') or obj.get('parentId')
    message = obj.get('message')
    stamp = obj.get('timestamp') or (message.get('timestamp') if isinstance(message, dict) else None)
    return dict(seq=seq, source_event_id=str(native_id) if native_id is not None else None,
                parent_event_id=str(parent_id) if parent_id is not None else None,
                role=role, timestamp=str(stamp) if stamp is not None else None, text=text,
                source_locator='line:' + str(seq), attachments=image_parts(content, cwd))


def persist(db, key, events, version):
    db.execute('INSERT OR REPLACE INTO event_state VALUES (?,?,?)', (key, stable_id(key), version))
    db.execute('DELETE FROM session_events WHERE session_key=?', (key,))
    db.execute('DELETE FROM artifacts WHERE session_key=?', (key,))
    for seq, event in enumerate(events, 1):
        # Source IDs are preferred. Position + content distinguish repeated text
        # in older formats; rewriting those sources may create new event IDs.
        identity = event['source_event_id'] or 'record:' + event['source_locator'] + ':' + stable_id(event['text'])
        if event.get('identity_suffix'):
            identity += '#part:' + event['identity_suffix']
        event_id = stable_id(key, identity)
        parent = stable_id(key, event['parent_event_id']) if event['parent_event_id'] else None
        db.execute('''INSERT OR REPLACE INTO session_events
                   (id,session_key,seq,source_event_id,parent_event_id,role,timestamp,text,source_locator,kind,metadata)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                   (event_id, key, seq, event['source_event_id'], parent,
                    event['role'], event['timestamp'], event['text'], event['source_locator'],
                    event.get('kind', 'message'), json.dumps(event.get('metadata', {}), ensure_ascii=False)))
        for i, image in enumerate(event.get('attachments', [])):
            db.execute('INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)',
                       (stable_id(event_id, i), key, event_id, image['mime'], image['name'],
                        image['local_path'], image['embedded'], image['reason']))
