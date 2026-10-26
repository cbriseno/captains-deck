"""Local platform capabilities; no service discovery or implicit configuration."""
from contextlib import contextmanager
import os
from pathlib import Path


def endpoint_path(home, name):
    """Existing home endpoints win; a separate code root is an explicit fallback."""
    local = Path(home) / 'bin' / name
    root = os.environ.get('FM_ROOT_OVERRIDE', '')
    if local.is_file() or not root:
        return str(local)
    return str(Path(root).expanduser() / 'bin' / name)


def shell_command(script, *args):
    if os.name == 'nt':
        raise OSError('Firstmate shell endpoints require a POSIX backend; run Deck with Firstmate in WSL.')
    return [os.environ.get('FM_BASH') or 'bash', str(script), *args]


def safe_file_reads():
    """Do not replace descriptor-relative, no-follow reads with path checks."""
    return (os.open in os.supports_dir_fd and
            all(hasattr(os, flag) for flag in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK')))


def private_directory(kind):
    """Keep deployed Linux locations; use native user directories elsewhere."""
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'captains-deck-web' / kind
    if kind == 'data':
        return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'captains-deck-web'
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state') / 'captains-deck-web'


@contextmanager
def file_lock(path, blocking=True):
    """A lifetime lock, interoperable with the previous POSIX flock wrapper."""
    with open(path, 'a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0, 2)
            if not stream.tell():
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            try:
                yield stream
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            try:
                yield stream
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
