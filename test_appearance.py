import http.client
import importlib.util
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class AppearanceTests(unittest.TestCase):
    def setUp(self):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        journal = patch.object(app, 'JOURNAL', Path(state.name) / 'moves.jsonl')
        journal.start()
        self.addCleanup(journal.stop)

    def page(self):
        server = app.LocalServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            port = server.server_port
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            connection.request('GET', '/', headers={'Host': f'127.0.0.1:{port}'})
            body = connection.getresponse().read().decode()
            connection.close()
            return body
        finally:
            server.shutdown()
            server.server_close()

    def test_appearance_follows_the_system_until_chosen(self):
        self.assertEqual(app.settings()['appearance'], 'system')
        self.assertEqual(app.save_settings({'appearance': 'dark'})['appearance'], 'dark')
        for value in ('blue', True, None, '<script>'):
            with self.assertRaises(ValueError):
                app.save_settings({'appearance': value})
        with self.assertRaises(ValueError):
            app.save_settings({'kindleDetection': 'dark'})
        self.assertEqual(app.settings()['appearance'], 'dark')

    def test_theme_must_be_one_koshelf_offers(self):
        self.assertEqual(app.settings()['theme'], 'koshelf')
        self.assertEqual(app.save_settings({'theme': 'rose-pine'})['theme'], 'rose-pine')
        for value in ('solarized', '../x', 1):
            with self.assertRaises(ValueError):
                app.save_settings({'theme': value})

    def test_unknown_saved_appearance_falls_back_to_system(self):
        app.JOURNAL.with_name('settings.json').write_text('{"appearance": "\\"><script>"}')
        self.assertEqual(app.settings()['appearance'], 'system')

    def test_page_carries_the_saved_appearance(self):
        self.assertIn('<html lang="en" data-appearance="system" data-palette="koshelf">', self.page())
        app.save_settings({'appearance': 'light', 'theme': 'gruvbox'})
        page = self.page()
        self.assertIn('<html lang="en" data-appearance="light" data-palette="gruvbox">', page)
        # theme.js runs before the stylesheet's first paint; app.js waits for the page.
        self.assertRegex(page, r'<script src="/theme.js"></script><script src="/app.js" defer>')


class ThemeTokenTests(unittest.TestCase):
    def test_every_color_comes_from_a_theme_token(self):
        css = (app.STATIC / 'style.css').read_text()
        blocks = re.findall(r'(:root,\[data-theme="light"\]|\[data-theme="dark"\])\{([^}]*)\}', css)[:2]
        self.assertEqual([name for name, _ in blocks], [':root,[data-theme="light"]', '[data-theme="dark"]'])
        light, dark = (set(re.findall(r'--([\w-]+):', body)) for _, body in blocks)
        self.assertEqual(light, dark, 'the dark theme must define every light token')
        rules = css[css.index('}', css.index('[data-theme="dark"]')) + 1:]
        rules = re.sub(r'url\([^)]*\)', '', rules)
        self.assertEqual(re.findall(r'#[0-9a-fA-F]{3,8}\b|rgba?\(|:\s*(?:white|black)\b', rules), [])
        used = set(re.findall(r'var\(--([\w-]+)\)', css + (app.STATIC / 'app.js').read_text()))
        # Reading progress colors are set per element from theme tokens; the rest is layout.
        self.assertEqual(used - light - {'reading-ink', 'reading-fill', 'title-inset', 'bar-height', 'serif', 'sans', 'mono'}, set())


def load_theme_builder():
    spec = importlib.util.spec_from_file_location('build_themes', Path(app.__file__).parent / 'scripts' / 'build-themes.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GeneratedThemeTests(unittest.TestCase):
    def setUp(self):
        self.builder = load_theme_builder()

    def test_themes_css_is_up_to_date(self):
        self.assertEqual((app.STATIC / 'themes.css').read_text(), self.builder.css(),
                         'Run python3 scripts/build-themes.py')

    def test_every_theme_is_offered_and_complete(self):
        themes = list(self.builder.THEMES)
        self.assertEqual(app.SETTINGS_CHOICES['theme'], ('koshelf', *themes))
        page = (app.STATIC / 'index.html').read_text()
        self.assertEqual(re.findall(r'name="theme" value="([\w-]+)"', page), ['koshelf', *themes])
        css = (app.STATIC / 'style.css').read_text()
        light = set(re.findall(r'--([\w-]+):', re.search(r':root,\[data-theme="light"\]\{([^}]*)\}', css).group(1)))
        for theme in themes:
            for mode in ('light', 'dark'):
                tokens = self.builder.tokens(self.builder.THEMES[theme][mode], mode == 'dark')
                self.assertEqual(set(tokens), light, (theme, mode))
                # Body text, secondary text, and the accent stay readable on the page.
                self.assertGreaterEqual(self.builder.contrast(tokens['ink'], tokens['paper']), 4.5, (theme, mode))
                self.assertGreaterEqual(self.builder.contrast(tokens['ink-soft'], tokens['paper']), 4.5, (theme, mode))
                self.assertGreaterEqual(self.builder.contrast(tokens['green'], tokens['paper']), 3.5, (theme, mode))
                self.assertGreaterEqual(self.builder.contrast(tokens['on-accent'], tokens['green']), 4.5, (theme, mode))


class SceneryArtTests(unittest.TestCase):
    def test_art_is_credited_safe_and_used(self):
        builder = load_theme_builder()
        art = sorted(builder.ART.rglob('*.svg'))
        self.assertTrue(art)
        # Each file is credited in a backticked table cell, by name or by a pattern such as everforest/dark/snow-*.svg.
        credited = re.findall(r'`([\w/*.-]+\.svg)`', (builder.ART / 'CREDITS.md').read_text())
        for path in art:
            name = path.relative_to(builder.ART).as_posix()
            self.assertTrue(any(Path(name).match(pattern) for pattern in credited), f'{name} is not in CREDITS.md')
            svg = path.read_text()
            # Gradients may refer to their own definitions, url(#id), but nothing outside the file.
            self.assertNotRegex(svg, r'(?i)<script|foreignObject|href\s*=|<image|@import|url\((?!#[\w-]+\))|<!DOCTYPE|<!ENTITY|<style', name)
            # Art drawn by make-scenery.py is larger, since it is a whole scene.
            self.assertLess(len(svg), 64_000 if 'make-scenery.py' in svg else 40_000, name)
        css = (app.STATIC / 'themes.css').read_text()
        self.assertNotIn('{{', css)
        defined = set(re.findall(r'--(art-[\w-]+):', css))
        # Every art variable used is defined, and every one defined is used.
        self.assertEqual(set(re.findall(r'var\(--(art-[\w-]+)\)', css)), defined)


if __name__ == '__main__':
    unittest.main()
