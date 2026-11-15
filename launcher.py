#!/usr/bin/env python3
"""Start KoShelf or reuse the already-running instance."""
import argparse
import json
from pathlib import Path
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
import local_session


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def running_identity(port, challenge):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    url = f'http://127.0.0.1:{port}/api/identity?challenge={challenge}'
    try:
        with opener.open(url, timeout=2) as response:
            identity = json.loads(response.read(4096))
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return None
        raise
    if not isinstance(identity, dict) or identity.get('app') != 'KoShelf' or identity.get('version') != 3:
        raise ValueError('Unexpected service identity')
    return identity


def main():
    parser=argparse.ArgumentParser(prog='koshelf', description='Open the KoShelf desktop app or launch the local browser interface.')
    parser.add_argument('--browser', action='store_true', help='Open the browser version instead of the macOS app')
    parser.add_argument('--library',help='Open a specific folder instead of the Mac library')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('The port must be between 0 and 65535.')
    native = Path.home() / 'Applications' / 'KoShelf.app'
    if sys.platform == 'darwin' and native.is_dir() and not args.browser and not args.library and args.port == 8765:
        subprocess.run(['/usr/bin/open', str(native)], check=True)
        print('Opened KoShelf.app')
        return
    challenge = secrets.token_hex(32)
    try:
        identity = running_identity(args.port, challenge) if args.port else None
    except (ValueError,OSError):
        parser.exit(1, 'This port is occupied by an unrecognized service. Stop it or use --port 8766.\n')
    if identity is not None:
        try:
            secret = local_session.ticket_secret(args.port)
            proof = identity.get('proof')
            if not local_session.valid_secret(proof) or not secrets.compare_digest(proof, local_session.proof(secret, args.port, challenge)):
                raise ValueError('Unexpected service proof')
        except (OSError, ValueError):
            parser.exit(1, 'Could not verify a browser session on this port. Stop it or use --port 8766.\n')
        if args.library:
            parser.exit(1, 'KoShelf is already running. Choose the folder in its interface, or use another --port.\n')
        local_session.open_browser(args.port, secret)
        print(f'Opened existing KoShelf: http://127.0.0.1:{args.port}')
        return
    command=[sys.executable,str(Path(__file__).with_name('app.py')),'--open','--port',str(args.port)]
    if args.library:command+=['--library',args.library]
    # Remain attached to the terminal so Control-C stops the local service.
    import os
    os.execv(sys.executable,command)

if __name__=='__main__':main()
