"""Browser handoff, shared data folder, and service lock."""
import http.client
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import kindle_delivery
import local_session

SECRET = 'a' * 64


class HandoffTests(unittest.TestCase):
    def test_code_opens_the_session_once(self):
        used = {}
        code = local_session.handoff_code(SECRET, 8000, now=1000)
        self.assertTrue(local_session.redeem_handoff(SECRET, 8000, code, used, now=1010))
        self.assertFalse(local_session.redeem_handoff(SECRET, 8000, code, used, now=1011))

    def test_expired_forged_or_foreign_codes_fail(self):
        code = local_session.handoff_code(SECRET, 8000, now=1000)
        self.assertFalse(local_session.redeem_handoff(SECRET, 8000, code, {}, now=1000 + local_session.HANDOFF_LIFETIME + 1))
        self.assertFalse(local_session.redeem_handoff(SECRET, 8001, code, {}, now=1001))
        self.assertFalse(local_session.redeem_handoff('b' * 64, 8000, code, {}, now=1001))
        forged = code[:-1] + ('0' if code[-1] != '0' else '1')
        self.assertFalse(local_session.redeem_handoff(SECRET, 8000, forged, {}, now=1001))
        for bad in ('', 'x', '1.2.3', None):
            self.assertFalse(local_session.redeem_handoff(SECRET, 8000, bad, {}, now=1001))

    def test_spent_codes_are_forgotten_after_they_expire(self):
        used = {}
        code = local_session.handoff_code(SECRET, 8000, now=1000)
        local_session.redeem_handoff(SECRET, 8000, code, used, now=1001)
        local_session.redeem_handoff(SECRET, 8000, 'x', used, now=2000)
        self.assertEqual(used, {})

    def test_linux_opens_a_link_instead_of_a_temporary_file(self):
        with patch.object(local_session.sys, 'platform', 'linux'), patch.object(local_session.webbrowser, 'open') as opened:
            self.assertIsNone(local_session.open_browser(8000, SECRET))
        url = opened.call_args[0][0]
        self.assertTrue(url.startswith('http://127.0.0.1:8000/open?code='))
        self.assertNotIn(SECRET, url)


class ConnectionOwnerTests(unittest.TestCase):
    def test_owner_is_read_from_the_browser_side_of_the_connection(self):
        with tempfile.NamedTemporaryFile('w', delete=False) as table:
            table.write('  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n'
                        '   0: 0100007F:1F90 0100007F:C350 01 00000000:00000000 00:00000000 00000000     0        0 1\n'
                        '   1: 0100007F:C350 0100007F:1F90 01 00000000:00000000 00:00000000 00000000  1000        0 2\n')
        self.addCleanup(os.unlink, table.name)
        # Browser port 0xC350 (50000) connected to service port 0x1F90 (8080).
        self.assertEqual(local_session.connection_uid(50000, 8080, tables=(table.name,)), 1000)
        self.assertIsNone(local_session.connection_uid(50001, 8080, tables=(table.name,)))
        self.assertIsNone(local_session.connection_uid(50000, 8080, tables=('/nonexistent',)))


class OpenLinkTests(unittest.TestCase):
    def setUp(self):
        self.server = app.LocalServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def open(self, code, owner):
        port = self.server.server_port
        with patch.object(local_session, 'connection_uid', return_value=owner):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            connection.request('GET', f'/open?code={code}', headers={'Host': f'127.0.0.1:{port}'})
            response = connection.getresponse()
            response.read()
            connection.close()
        return response

    def test_link_redirects_only_for_this_user_and_only_once(self):
        port, secret = self.server.server_port, self.server.session_secret
        code = local_session.handoff_code(secret, port)
        self.assertEqual(self.open(code, os.getuid() + 1).status, 403)
        response = self.open(code, os.getuid())
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader('Location'), '/#session=' + secret)
        self.assertEqual(self.open(code, os.getuid()).status, 403)


class ServiceLifetimeTests(unittest.TestCase):
    def test_app_service_stops_when_the_app_is_gone(self):
        stopped = threading.Event()
        server = type('Server', (), {'shutdown': lambda self: stopped.set()})()
        with patch.object(app.os, 'getppid', side_effect=[100, 100, 1, 1, 1]):
            app.exit_with_parent(server, interval=0.01)
            self.assertTrue(stopped.wait(2))

    def test_unusable_data_folder_exits_with_a_message(self):
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o775)
            with patch.dict(os.environ, {'KOSHELF_STATE_DIR': folder}), patch.object(app, 'JOURNAL', Path(folder) / 'moves.jsonl'), \
                    patch.object(app.sys, 'argv', ['app.py']), self.assertRaises(SystemExit) as exit:
                app.main()
            self.assertEqual(exit.exception.code, app.STATE_FOLDER_UNUSABLE)


class StateFolderTests(unittest.TestCase):
    def test_default_folder_is_shared_with_the_app(self):
        with patch.object(app.os.sys, 'platform', 'darwin'):
            self.assertEqual(app.default_state_directory(), Path.home() / 'Library/Application Support/KoShelf')
        with patch.object(app.os.sys, 'platform', 'linux'), patch.dict(os.environ, {'XDG_STATE_HOME': '/xdg/state'}):
            self.assertEqual(app.default_state_directory(), Path('/xdg/state/koshelf'))
        with patch.object(app.os.sys, 'platform', 'linux'), patch.dict(os.environ, {'XDG_STATE_HOME': ''}):
            self.assertEqual(app.default_state_directory(), Path.home() / '.local/state/koshelf')

    def test_only_one_service_uses_a_data_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            first = app.lock_state_directory(Path(folder))
            self.assertIsNotNone(first)
            self.assertIsNone(app.lock_state_directory(Path(folder)))
            os.close(first)
            second = app.lock_state_directory(Path(folder))
            self.assertIsNotNone(second)
            os.close(second)


class ConverterSandboxTests(unittest.TestCase):
    def test_converter_runs_unsandboxed_only_where_no_sandbox_exists(self):
        with patch.object(kindle_delivery, 'SANDBOX_EXEC', '/nonexistent/sandbox-exec'):
            self.assertFalse(kindle_delivery.sandbox_available())
            self.assertEqual(kindle_delivery.sandboxed('/converter', '/work'), ['/converter'])


if __name__ == '__main__':
    unittest.main()
