"""One native Hangul export at a time on this PC: builds of other restoration jobs wait their turn.

The export itself (runtime/hwp_automation/scripts/native_layout*.py) refuses to start while another export
holds its session lock or while any Hwp.exe is running. Five jobs reaching hwp_build together had four of
the builds fail at once; here they queue instead. A Hangul the user has open is not waited for: with no
export in progress it is reported by the export as before.
"""
import calendar
import json
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

QUEUE = 'hwp-restoration-native-queue.lock'
SESSION = 'hwp-automation-native-session.lock'  # the export's own lock, in the same folder
RUNNING = 'hwp-restoration-export-'              # + run name + .json: where an export in progress keeps its files
WAIT_SECONDS = 600    # an export takes about 25s to 60s, so several jobs ahead still fit
SETTLE_SECONDS = 20   # the Hangul of the export before this one may take a moment to exit
POLL_SECONDS = 1.0


class QueueTimeout(RuntimeError):
    """Other jobs kept the export busy for the whole wait."""


def marker_path(run_dir):
    """Beside the run folder, which the export requires to be empty."""
    return Path(str(run_dir) + '.queue.json')


def queue_state(run_dir):
    """{'state': 'waiting'|'exporting', 'at': epoch seconds} or None when this build never queued."""
    try: return json.loads(marker_path(run_dir).read_text(encoding='utf-8'))
    except (OSError, ValueError): return None


def _hwp_running():
    try:
        import psutil
        return any((p.info.get('name') or '').lower() == 'hwp.exe' for p in psutil.process_iter(['name']))
    except Exception:
        return False  # the export runs its own authoritative preflight


def _lock_held(path):
    """Is the lock file there and its owner still running? A lock whose owner was killed is not held.
    Windows hands a dead process's number to a new one within seconds (a restarted CLI starts dozens), so a
    running process with the owner's pid proves nothing: the owner must also be older than its lock."""
    try: owner = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError): return Path(path).exists()  # being written, or unreadable: assume held
    pid = owner.get('pid') if isinstance(owner, dict) else None
    try:
        import psutil
    except ImportError:
        return True
    if type(pid) is not int: return True
    try: born = psutil.Process(pid).create_time()
    except psutil.NoSuchProcess: return False
    except Exception: return True
    try: taken = calendar.timegm(time.strptime(owner.get('acquired_at', ''), '%Y-%m-%dT%H:%M:%SZ'))
    except (ValueError, TypeError): return True
    return born <= taken + 2


def _drop_stale(path):
    """Remove a lock whose owner is gone (see _lock_held). Only the unchanged file is removed."""
    try: seen = Path(path).read_bytes()
    except OSError: return
    if _lock_held(path): return
    try:
        if Path(path).read_bytes() == seen: Path(path).unlink()
    except OSError:
        pass


def owned_session_gone(run_dir):
    """True when the export of this run folder left no Hangul running. The export records the session it
    started (pid, program, start time) in ownership.json; a process that still matches all three is that hidden
    session and is closed here. It is never a Hangul the user opened."""
    try: identity = json.loads((Path(run_dir) / 'ownership.json').read_text(encoding='utf-8'))
    except OSError: return True   # the export never got as far as starting Hangul
    except ValueError: return False
    try:
        from runtime_paths import automation_root
        scripts = str(automation_root() / 'scripts')
        if scripts not in sys.path: sys.path.insert(0, scripts)
        from native_layout import cleanup_owned
        return cleanup_owned(identity) in ('terminated_owned_session', 'already_exited')
    except Exception:
        return False


def reap_orphans(folder=None):
    """Close hidden Hangul sessions left by exports that were interrupted (a stopped CLI kills the export but not
    the Hangul it started, and every later build on this PC then fails with existing_hwp_session).
    Call only while holding the export turn: no restoration export is then running. Returns the run folders cleared."""
    folder = Path(folder or tempfile.gettempdir()); cleared = []
    try:
        import psutil
        live = {p.pid for p in psutil.process_iter(['name']) if (p.info.get('name') or '').lower() == 'hwp.exe'}
    except Exception:
        return []
    if not live:  # nothing to close; pointers of exports that were cut short are no longer needed
        for pointer in folder.glob(RUNNING + '*.json'):
            try: pointer.unlink()
            except OSError: pass
        return []
    records = [r for pattern in ('hwp-single-*/ownership.json', 'hwp-restoration-*/ownership.json') for r in folder.glob(pattern)]
    for pointer in folder.glob(RUNNING + '*.json'):  # exports that never reached their end, wherever their run folder is
        try: records.append(Path(json.loads(pointer.read_text(encoding='utf-8'))['run_dir']) / 'ownership.json')
        except (OSError, ValueError, KeyError, TypeError): pass
    for record in records:
        try: pid = json.loads(record.read_text(encoding='utf-8')).get('pid')
        except (OSError, ValueError, AttributeError): continue
        if pid in live and owned_session_gone(record.parent): cleared.append(record.parent.name)
    for pointer in folder.glob(RUNNING + '*.json'):
        try:
            if owned_session_gone(json.loads(pointer.read_text(encoding='utf-8'))['run_dir']): pointer.unlink()
        except (OSError, ValueError, KeyError, TypeError): pass
    return cleared


def _release(lease, sleep=time.sleep):
    """Windows refuses to delete the lock file in the instant a waiting job is reading it; try again shortly.
    If it never goes, the file names this process as owner and is recovered by the next job once we exit."""
    for _ in range(100):
        try:
            lease.release(); return
        except PermissionError:
            sleep(0.02)
        except Exception:
            return


@contextmanager
def turn(run_dir, document, *, wait=WAIT_SECONDS, folder=None, sleep=time.sleep, clock=time.monotonic, hwp_running=_hwp_running):
    """Hold this PC's export turn for the body; raises QueueTimeout if it never comes. Yields whether it waited."""
    from runtime_paths import native_root
    if str(native_root()) not in sys.path: sys.path.insert(0, str(native_root()))
    from academy.documents.locks import AppSessionLock, LockBusyError  # recovers a lock whose owner has died
    folder = Path(folder or tempfile.gettempdir()); run_dir = Path(run_dir)
    lease = AppSessionLock(folder / QUEUE, run_id=run_dir.name, document_key=str(document))
    began = clock(); waited = False

    def note(state):
        try: marker_path(run_dir).write_text(json.dumps({'state': state, 'at': time.time()}), encoding='utf-8')
        except OSError: pass  # status reporting only
    while True:
        try:
            lease.acquire(); break
        except LockBusyError:
            _drop_stale(folder / QUEUE)  # an export killed with its CLI; the lock's own recovery trusts a reused pid
            if clock() - began >= wait: raise QueueTimeout('native_export_queue_timeout') from None
            if not waited: note('waiting'); waited = True
            sleep(POLL_SECONDS)
    try:
        # The previous export released its turn but its Hangul may still be closing, or an export that is
        # not a restoration build holds the session lock. A short, bounded wait; then the export decides.
        settle = clock()
        while _lock_held(folder / SESSION) and clock() - settle < SETTLE_SECONDS: sleep(POLL_SECONDS)
        if not _lock_held(folder / SESSION):
            _drop_stale(folder / SESSION)  # else the export fails once with "session lock is busy"
            reap_orphans(folder)
        while waited and hwp_running() and clock() - settle < SETTLE_SECONDS: sleep(POLL_SECONDS)
        note('exporting')
        pointer = folder / f'{RUNNING}{run_dir.name}.json'
        try: pointer.write_text(json.dumps({'run_dir': str(run_dir)}), encoding='utf-8')
        except OSError: pass
        yield waited
        try: pointer.unlink()  # the export ended and closed its own Hangul; an interrupted one leaves the pointer
        except OSError: pass
    finally:
        _release(lease)
        try: marker_path(run_dir).unlink()
        except OSError: pass
