"""One cooperating index writer across CLI and desktop processes."""

from contextlib import contextmanager
from pathlib import Path
import os


@contextmanager
def writer_lock(db):
    path = db.execute('PRAGMA database_list').fetchone()[2]
    if not path:
        yield
        return
    lock_path = Path(path + '.writer.lock')
    with lock_path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if stream.seek(0, 2) == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
