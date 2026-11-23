"""Read Codex session names without modifying its metadata or transcripts."""

import json
from pathlib import Path
import sqlite3

from .core import now


def _text(value):
    return value.strip().replace('\r', ' ').replace('\n', ' ') if isinstance(value, str) else ''


def codex_home(root):
    root = Path(root).expanduser().resolve()
    return next((path.parent for path in (root, *root.parents)
                 if path.name in ('sessions', 'archived_sessions')), root)


def codex_state_path(home):
    candidates = [path for path in home.glob('state_*.sqlite') if path.stem[6:].isdigit()]
    return max(candidates, key=lambda path: int(path.stem[6:]), default=None)


def load_codex_titles(root):
    home = codex_home(root)
    indexed = {}
    try:
        with (home / 'session_index.jsonl').open(encoding='utf8') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get('id'), str) and 'thread_name' in row:
                    if row['thread_name'] is None or isinstance(row['thread_name'], str):
                        indexed[row['id']] = _text(row['thread_name']) or None
    except (OSError, UnicodeError):
        pass
    states = {}
    try:
        state = codex_state_path(home)
        if state:
            db = sqlite3.connect(state.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
            try:
                columns = {row[1] for row in db.execute('PRAGMA table_info(threads)')}
                if {'id', 'title'} <= columns:
                    name = 'name' if 'name' in columns else 'NULL'
                    for session_id, explicit_name, title in db.execute(f'SELECT id,{name},title FROM threads'):
                        if isinstance(session_id, str):
                            states[session_id] = (_text(explicit_name), _text(title))
            finally:
                db.close()
    except (OSError, sqlite3.Error):
        pass
    # Explicit DB names win; older versions keep renamed sessions in the JSONL index.
    titles = dict(indexed)
    for session_id, (name, title) in states.items():
        titles[session_id] = name or indexed.get(session_id) or title or None
    return titles


def refresh_codex_titles(db, root, titles=None):
    titles = load_codex_titles(root) if titles is None else titles
    if not titles:
        return set()
    root = Path(root).expanduser().resolve()
    changed = set()
    for row in db.execute("SELECT key,native_id,source,title FROM sessions WHERE agent='codex'").fetchall():
        if row['native_id'] not in titles:
            continue
        try:
            Path(row['source']).resolve().relative_to(root)
        except (ValueError, OSError):
            continue
        title = titles[row['native_id']]
        if title is None:
            first = db.execute("""SELECT text FROM session_events
                WHERE session_key=? AND role='user' AND kind='message' AND text!='' ORDER BY seq LIMIT 1""",
                               (row['key'],)).fetchone()
            title = (first['text'] if first else row['native_id']).replace('\n', ' ')[:160]
        if title != row['title']:
            db.execute('UPDATE sessions SET title=?,indexed_at=? WHERE key=?', (title, now(), row['key']))
            changed.add(row['key'])
    return changed
