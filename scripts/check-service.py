"""Smoke-test the packaged service on a temporary library, then terminate it."""
import json
import os
import platform
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

project = Path(__file__).resolve().parents[1]
bundle = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else project/'build/KoShelf.app'
resources = bundle/'Contents/Resources'
bundled_python = resources/'Python'/platform.machine()/'bin/python3'
if len(sys.argv) > 1 and not bundled_python.is_file():
    raise SystemExit(f'Bundled Python is missing: {bundled_python}')
python = bundled_python if bundled_python.is_file() else sys.executable

with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary)/'books';root.mkdir()
    home=Path(temporary)/'home';home.mkdir()
    # KoShelf's data folder holds service.lock, so keep it apart from the library under test.
    state=Path(temporary)/'state'
    env={**os.environ,'HOME':str(home),'KOSHELF_STATE_DIR':str(state),'PYTHONDONTWRITEBYTECODE':'1'}
    app=resources/'app.py'
    process=subprocess.Popen([str(python),'-E','-S','-B',str(app),'--library',str(root),'--port','0','--session-pipe'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    try:
        line=process.stdout.readline().strip()
        assert line.startswith('KoShelf: http://127.0.0.1:'),line
        url=line.split('KoShelf: ',1)[1]
        handoff = process.stdout.readline().strip()
        assert handoff.startswith('KoShelf session: '), 'Missing private session handoff'
        bootstrap = urllib.parse.urlsplit(handoff.split('KoShelf session: ', 1)[1])
        secret = urllib.parse.parse_qs(bootstrap.fragment)['session'][0]
        authorized = {'Authorization': 'Bearer ' + secret}
        assert bootstrap.hostname == '127.0.0.1' and len(secret) == 64

        def request(endpoint, headers=None, body=None):
            req = urllib.request.Request(url + endpoint, data=body, headers=headers or {})
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)

        def forbidden(endpoint, headers=None, body=None):
            try:
                request(endpoint, headers, body)
            except urllib.error.HTTPError as exc:
                assert exc.code == 403
                assert 'Access-Control-Allow-Origin' not in exc.headers
            else:
                raise AssertionError('Private request unexpectedly succeeded')

        def post(endpoint, payload):
            return request(endpoint, {**authorized, 'Content-Type': 'application/json', 'Origin': url},
                           json.dumps(payload).encode())

        assert request('/api/identity') == {'app':'KoShelf', 'version':3}
        forbidden('/api/library')
        forbidden('/api/sync-history')
        forbidden('/api/sync-status')
        forbidden('/api/warm-kindle')
        forbidden('/api/cover?path=Book.epub')
        forbidden('/api/font-preview')
        forbidden('/api/library', {**authorized, 'Host': 'example.test'})
        forbidden('/api/library', {**authorized, 'Sec-Fetch-Site': 'cross-site'})
        forbidden('/api/folders', {'Content-Type': 'application/json', 'Origin': url},
                  json.dumps({'library': str(root.resolve()), 'parent': '', 'name':'Forged folder'}).encode())
        assert not (root/'Forged folder').exists()
        forbidden('/api/folders', {**authorized, 'Content-Type': 'application/json', 'Origin': 'https://example.test'},
                  json.dumps({'library': str(root.resolve()), 'parent': '', 'name':'Cross-site folder'}).encode())
        assert not (root/'Cross-site folder').exists()

        library = request('/api/library', authorized)
        assert library['connected'] and library['books'] == []
        assert post('/api/folders', {'library':library['root'],'parent':'','name':'Test folder'})['folder'] == 'Test folder'
        assert (root/'Test folder').is_dir()
        (root/'Test folder'/'sample.txt').write_text('backup fixture')
        for copied, skipped in [(1, 0), (0, 1)]:
            backup = post('/api/backup', {'library':library['root']})
            assert (backup['copied'],backup['skipped'],backup['failed'])==(copied,skipped,0),backup
        assert (home/'KoShelf Library'/'Test folder'/'sample.txt').read_text()=='backup fixture'
        mac = str((home/'KoShelf Library').resolve())
        assert post('/api/library', {'path': mac})['root'] == mac
        preview = post('/api/genre-cleanup-preview', {'library': mac, 'scope': 'library'})
        assert preview['scanned'] == 0 and 'Science Fiction' in preview['genres'], preview
        assert not (state/'genre-mappings.json').exists()
        cloud = (home/'Library'/'Mobile Documents'/'com~apple~CloudDocs'/'KoShelf Library').resolve()
        cloud.mkdir(parents=True)
        (cloud/'Phone.epub').write_bytes(b'cloud sync fixture')
        assert post('/api/icloud-setup', {'library': mac, 'path': str(cloud)})['available']
        review = post('/api/icloud-sync', {'library': mac})
        assert review['actions'][0]['source'] == 'icloud', review
        result = post('/api/icloud-sync', {'library': mac, 'token': review['token'], 'choices': {}})
        assert result['applied'] == 2 and not result['errors'], result
        assert (Path(mac)/'Phone.epub').read_bytes() == b'cloud sync fixture'
        assert (cloud/'Test folder').is_dir()
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10)==0
        assert secret not in process.stderr.read(), 'Session credential leaked into the service log'
        print('Packaged service: private session, spoofed request rejection, library, folder creation, backup/deduplication, genre preview and graceful SIGTERM shutdown passed.')
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=10)
