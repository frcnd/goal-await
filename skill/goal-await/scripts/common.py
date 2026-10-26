"""Minimal local persistence and locking; Python standard library only."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@contextmanager
def exclusive(path):
    """OS lock releases on process exit; the lock file is intentionally retained."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open('a+b')
    f.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise RuntimeError('Another executor owns this run/thread')
    try:
        yield
    finally:
        f.seek(0)
        if os.name == 'nt':
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


def log_event(root, kind, **data):
    with (Path(root) / 'events.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps({'time': time.time(), 'kind': kind, **data}, ensure_ascii=False) + '\n')
        f.flush()


def wake_once(state, generation):
    """Caller holds exclusive run/thread lock; persistence precedes dispatch."""
    if state['state'] != 'WAITING' or state['generation'] != generation:
        return False
    state['state'] = 'RUNNING'
    state['wake_count'] += 1
    state['last_wake_generation'] = generation
    state['condition_met_at'] = time.time()
    return True
