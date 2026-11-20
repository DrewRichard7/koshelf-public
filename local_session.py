"""Browser handoff that keeps the active shelf credential off the command line."""
import hmac
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import threading
import time
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


HANDOFF_LIFETIME = 60


def handoff_code(secret, port, now=None):
    """A single-use code that opens the session, valid for one minute."""
    expiry = int(now if now is not None else time.time()) + HANDOFF_LIFETIME
    nonce = secrets.token_hex(16)
    signature = hmac.digest(secret.encode('ascii'), f'KoShelf-open:{port}:{expiry}:{nonce}'.encode('ascii'), 'sha256').hex()
    return f'{expiry}.{nonce}.{signature}'


def redeem_handoff(secret, port, code, used, now=None):
    """Check a handoff code once. `used` maps spent nonces to their expiry."""
    now = int(now if now is not None else time.time())
    for nonce in [nonce for nonce, expiry in used.items() if expiry < now]:
        del used[nonce]
    match = re.fullmatch(r'([0-9]{1,12})\.([0-9a-f]{32})\.([0-9a-f]{64})', code or '')
    if not match:
        return False
    expiry, nonce, signature = int(match[1]), match[2], match[3]
    expected = hmac.digest(secret.encode('ascii'), f'KoShelf-open:{port}:{expiry}:{nonce}'.encode('ascii'), 'sha256').hex()
    if (not hmac.compare_digest(signature, expected) or not now <= expiry <= now + HANDOFF_LIFETIME
            or nonce in used):
        return False
    used[nonce] = expiry
    return True


def connection_uid(client_port, server_port, tables=('/proc/net/tcp', '/proc/net/tcp6')):
    """On Linux, the user that owns a loopback connection to this service, or None."""
    for table in tables:
        try:
            with open(table, encoding='ascii') as stream:
                rows = stream.read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            if len(fields) < 8:
                continue
            try:
                local_port = int(fields[1].rsplit(':', 1)[1], 16)
                remote_port = int(fields[2].rsplit(':', 1)[1], 16)
                uid = int(fields[7])
            except (IndexError, ValueError):
                continue
            # The browser's side of the connection: its port, connected to ours.
            if local_port == client_port and remote_port == server_port:
                return uid
    return None


def open_browser(port, secret):
    if sys.platform.startswith('linux'):
        # Snap browsers can't read files in /tmp, so Linux opens a single-use
        # link instead. It expires after a minute, stops working once used, and
        # the service accepts it only from a connection owned by this user.
        webbrowser.open(f'http://127.0.0.1:{port}/open?code={handoff_code(secret, port)}')
        return None
    # Default-browser helpers may expose their arguments to other OS users.
    # Only the browser running as this user can read the redirect file.
    redirect = session_directory() / f'open-{port}.html'
    url = session_url(port, secret)
    write_private(redirect, '<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
                  f'<meta http-equiv="refresh" content="0;url={url}"><a href="{url}">Open KoShelf</a>')
    webbrowser.open(redirect.as_uri())
    return redirect


def forget_redirect_later(redirect, delay=60):
    """Remove the credential-bearing redirect once the browser has had time to read it."""
    if redirect is None:
        return
    timer = threading.Timer(delay, lambda: redirect.unlink(missing_ok=True))
    timer.daemon = True
    timer.start()


def forget(port, ticket):
    ticket.unlink(missing_ok=True)
    (ticket.parent / f'open-{port}.html').unlink(missing_ok=True)
