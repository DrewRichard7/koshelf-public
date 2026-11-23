"""Filesystem identity checks shared by library operations."""

import os
import stat
from contextlib import contextmanager
from io import FileIO


@contextmanager
def open_regular(path):
    """Open a regular file without following links or blocking on a FIFO."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with FileIO(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Choose a regular file.')
        yield stream


def paths_overlap(first, second):
    """Find overlapping roots even when the filesystem aliases their spelling."""
    first, second = first.resolve(), second.resolve()
    if first.is_relative_to(second) or second.is_relative_to(first):
        return True
    for path, other in ((first, second), (second, first)):
        try:
            reference = path.stat()
        except FileNotFoundError:
            continue
        for ancestor in (other, *other.parents):
            try:
                if os.path.samestat(reference, ancestor.stat()):
                    return True
            except FileNotFoundError:
                continue
    return False


def private_state_directory(path, *, create=True):
    """Accept an owned state directory that other local users cannot modify."""
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o022):
        raise ValueError('Choose a KoShelf data folder owned and writable only by you.')
    return path


def private_state_subdirectory(base, *parts):
    """Keep KoShelf-managed subdirectories private, including existing data."""
    path = private_state_directory(base)
    for part in parts:
        path = private_state_directory(path / part)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchmod(fd, 0o700)
        finally:
            os.close(fd)
    return path


def open_state_text(path, mode='r'):
    """Open private state without following links or blocking on special files."""
    reading = mode == 'r'
    private_state_directory(path.parent, create=not reading)
    flags = os.O_RDONLY if reading else os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('KoShelf data files must be regular files owned only by you.')
        if info.st_mode & 0o077:
            os.fchmod(fd, 0o600)
        return os.fdopen(fd, mode, encoding='utf-8')
    except BaseException:
        os.close(fd)
        raise
