"""Browser handoff that keeps the active shelf credential off the command line."""
import hmac
import os
from pathlib import Path
import re
import stat
import tempfile
import webbrowser


def valid_secret(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def proof(secret, port, challenge):
    return hmac.digest(secret.encode('ascii'), f'KoShelf:{port}:{challenge}'.encode('ascii'), 'sha256').hex()


def session_directory():
    directory = Path(tempfile.gettempdir()) / f'koshelf-{os.getuid()}'
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError('KoShelf’s browser handoff directory is not private.')
    return directory


def write_private(path, content):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False, encoding='ascii') as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def publish(port, secret):
    ticket = session_directory() / f'session-{port}'
    write_private(ticket, secret)
    return ticket


def ticket_secret(port):
    path = session_directory() / f'session-{port}'
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, encoding='ascii') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError('KoShelf’s browser session file is not private.')
        secret = stream.read(65)
    if not valid_secret(secret):
        raise ValueError('KoShelf’s browser session file is invalid.')
    return secret


def session_url(port, secret):
    return f'http://127.0.0.1:{port}/#session={secret}'


def open_browser(port, secret):
    # Default-browser helpers may expose their arguments to other OS users.
    # Only the browser running as this user can read the redirect file.
    redirect = session_directory() / f'open-{port}.html'
    url = session_url(port, secret)
    write_private(redirect, '<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
                  f'<meta http-equiv="refresh" content="0;url={url}"><a href="{url}">Open KoShelf</a>')
    webbrowser.open(redirect.as_uri())


def forget(port, ticket):
    ticket.unlink(missing_ok=True)
    (ticket.parent / f'open-{port}.html').unlink(missing_ok=True)
