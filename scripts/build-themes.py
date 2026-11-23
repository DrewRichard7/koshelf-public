#!/usr/bin/env python3
"""Generate static/themes.css from the color themes below.

Each theme has a light and a dark variant, described by a few colors from its upstream palette.
This script derives every color token that style.css uses from them, so a new theme needs only
its palette here. Run it after changing a palette:

    python3 scripts/build-themes.py

tests/test_appearance.py fails when src/static/themes.css doesn't match this file.
The KoShelf theme itself is hand-tuned at the top of style.css and isn't generated.
"""
from pathlib import Path
import re
import urllib.parse

OUTPUT = Path(__file__).resolve().parent.parent / 'src' / 'static' / 'themes.css'
# Decorative SVGs, one folder per theme, with light/ and dark/ subfolders for art used in one mode.
# {{token}} placeholders take that mode's colors, because an SVG used as a CSS image can't read
# CSS variables. Credits are in CREDITS.md there.
ART = Path(__file__).resolve().parent / 'theme-art'

# Shared rules for themes with scenery. Decorations are fixed in the window margins, take no clicks,
# and move only with transform, so scrolling the shelf never repaints them. They rely on main and
# .window-bar having no transform, filter, or contain, which would make "fixed" relative to them.
# --fx-rail is where the shelf panel starts.
SCENERY = '''
{root}{--fx-rail:248px}
@media(max-width:900px){{root}{--fx-rail:195px}}
{root} body::before,{root} body::after,{root} .window-bar::before,{root} .window-bar::after,{root} .sidebar::before,{root} .sidebar::after{content:'';position:fixed;pointer-events:none;background-repeat:no-repeat}
{root} .waiting::before,{root} .waiting::after,{root} .waiting .window-bar::before,{root} .waiting .window-bar::after,{root} .waiting .sidebar::before,{root} .waiting .sidebar::after{display:none}
@media(max-width:600px){{root} body::before,{root} body::after,{root} .window-bar::before,{root} .window-bar::after,{root} .sidebar::before,{root} .sidebar::after{display:none}}
@keyframes fx-sway{from{rotate:-1.5deg}to{rotate:1.5deg}}
'''
# A layer of falling things, like snow: one tile beyond the window on the top and left, so
# moving it by one tile loops without a seam. It sits behind the shelf panel, in the margins.
def falling(selector, art, width, height, seconds, opacity, sway=0):
    # With sway, the layer drifts side to side on the way down; it still ends exactly one tile on.
    steps = ''.join(f'{k * 25}%{{transform:translate({width * k / 4 + (sway if k % 2 else 0):g}px,{height * k / 4:g}px)}}'
                    for k in (1, 2, 3)) if sway else ''
    return (f'{selector}{{inset:-{height}px 0 0 -{width}px;z-index:-1;background:{art} repeat;background-size:{width}px {height}px;'
            f'opacity:{opacity};animation:fx-fall-{width}-{height} {seconds}s linear infinite}}\n'
            f'@keyframes fx-fall-{width}-{height}{{{steps}to{{transform:translate({width}px,{height}px)}}}}\n')

# Rounded things that keep their shape when a theme squares off every corner: the pie chart,
# status dots, the sync spinner, and the stars in the light/dark switch.
ROUND = ':not(.genre-pie):not(.genre-slice):not(#status i):not(.genre-row i):not(.connection-pulse i):not(.sync-icon):not(.toggle-stars)'

# Keys: paper (main background), shell (window behind the content), sidebar, surface (buttons),
# field (text fields), line, ink (text), soft (secondary text), muted, faint, accent, ok (success),
# warn, danger, covers (five hues for generated covers), chart (eight chart colors), and optionally
# overrides (tokens to set directly instead of deriving them).
# A theme may also set fonts (--serif, --sans) and extra CSS, where {root} stands for its selector.
THEMES = {
    'tokyonight': {
        'name': 'Tokyo Night',
        'source': 'folke/tokyonight.nvim (Apache-2.0): Day and Night',
        'light': dict(paper='#e1e2e7', shell='#d0d5e3', sidebar='#d6d9e4', surface='#e9e9ed', field='#ececf0',
                      line='#c4c8da', ink='#3760bf', soft='#6172b0', muted='#848cb5', faint='#9aa1c4',
                      accent='#2e7de9', ok='#587539', warn='#8c6c3e', danger='#f52a65',
                      covers=['#2e7de9', '#9854f1', '#007197', '#587539', '#b15c00'],
                      chart=['#2e7de9', '#9854f1', '#007197', '#587539', '#b15c00', '#f52a65', '#118c74', '#8c6c3e']),
        'dark': dict(paper='#1a1b26', shell='#16161e', sidebar='#16161e', surface='#24283b', field='#1f2335',
                     line='#292e42', ink='#c0caf5', soft='#a9b1d6', muted='#737aa2', faint='#565f89',
                     accent='#7aa2f7', ok='#9ece6a', warn='#e0af68', danger='#f7768e',
                     covers=['#7aa2f7', '#bb9af7', '#7dcfff', '#9ece6a', '#ff9e64'],
                     chart=['#7aa2f7', '#bb9af7', '#7dcfff', '#9ece6a', '#ff9e64', '#f7768e', '#1abc9c', '#e0af68']),
    },
    'gruvbox': {
        'name': 'Gruvbox',
        'source': 'morhetz/gruvbox, as in ellisonleao/gruvbox.nvim (MIT)',
        'light': dict(paper='#fbf1c7', shell='#ebdbb2', sidebar='#f2e5bc', surface='#f9f5d7', field='#f9f5d7',
                      line='#d5c4a1', ink='#3c3836', soft='#504945', muted='#7c6f64', faint='#928374',
                      accent='#076678', ok='#79740e', warn='#b57614', danger='#9d0006',
                      covers=['#076678', '#8f3f71', '#427b58', '#79740e', '#af3a03'],
                      chart=['#076678', '#8f3f71', '#427b58', '#79740e', '#af3a03', '#9d0006', '#b57614', '#7c6f64']),
        'dark': dict(paper='#282828', shell='#1d2021', sidebar='#1d2021', surface='#3c3836', field='#32302f',
                     line='#3c3836', ink='#ebdbb2', soft='#d5c4a1', muted='#a89984', faint='#928374',
                     accent='#83a598', ok='#b8bb26', warn='#fabd2f', danger='#fb4934',
                     covers=['#83a598', '#d3869b', '#8ec07c', '#b8bb26', '#fe8019'],
                     chart=['#83a598', '#d3869b', '#8ec07c', '#b8bb26', '#fe8019', '#fb4934', '#fabd2f', '#a89984']),
    },
    'rose-pine': {
        'name': 'Rosé Pine',
        'source': 'rose-pine/neovim (MIT): Dawn and Main',
        'light': dict(paper='#faf4ed', shell='#f2e9e1', sidebar='#f4ede8', surface='#fffaf3', field='#fffaf3',
                      line='#dfdad9', ink='#464261', soft='#575279', muted='#797593', faint='#9893a5',
                      accent='#286983', ok='#56949f', warn='#ea9d34', danger='#b4637a',
                      covers=['#286983', '#907aa9', '#d7827e', '#56949f', '#ea9d34'],
                      chart=['#286983', '#907aa9', '#d7827e', '#56949f', '#ea9d34', '#b4637a', '#6d8f89', '#797593']),
        'dark': dict(paper='#191724', shell='#16141f', sidebar='#16141f', surface='#26233a', field='#1f1d2e',
                     line='#26233a', ink='#e0def4', soft='#c8c5e0', muted='#908caa', faint='#6e6a86',
                     accent='#c4a7e7', ok='#9ccfd8', warn='#f6c177', danger='#eb6f92',
                     covers=['#31748f', '#c4a7e7', '#ebbcba', '#9ccfd8', '#f6c177'],
                     chart=['#9ccfd8', '#c4a7e7', '#ebbcba', '#31748f', '#f6c177', '#eb6f92', '#95b1ac', '#908caa']),
    },
    'merino': {
        'name': 'Merino',
        'source': 'Merino Light and Dark, based on GitHub Primer colors (MIT)',
        'light': dict(paper='#ffffff', shell='#f6f8fa', sidebar='#f6f8fa', surface='#f6f8fa', field='#ffffff',
                      line='#d1d9e0', ink='#1f2328', soft='#424a53', muted='#59636e', faint='#818b98',
                      accent='#0969da', ok='#1a7f37', warn='#9a6700', danger='#cf222e',
                      covers=['#0969da', '#8250df', '#1b7c83', '#1a7f37', '#bc4c00'],
                      chart=['#0969da', '#8250df', '#1b7c83', '#1a7f37', '#bc4c00', '#cf222e', '#9a6700', '#6e7781']),
        'dark': dict(paper='#0d1117', shell='#010409', sidebar='#010409', surface='#21262d', field='#0d1117',
                     line='#30363d', ink='#e6edf3', soft='#c9d1d9', muted='#9198a1', faint='#6e7681',
                     accent='#4493f8', ok='#3fb950', warn='#d29922', danger='#f85149',
                     covers=['#58a6ff', '#bc8cff', '#39c5cf', '#3fb950', '#ffa657'],
                     chart=['#58a6ff', '#bc8cff', '#39c5cf', '#3fb950', '#ffa657', '#ff7b72', '#d29922', '#8b949e']),
    },
    'catppuccin': {
        'name': 'Catppuccin',
        'source': 'catppuccin/nvim (MIT): Latte and Mocha',
        'light': dict(paper='#eff1f5', shell='#dce0e8', sidebar='#e6e9ef', surface='#e6e9ef', field='#ffffff',
                      line='#ccd0da', ink='#4c4f69', soft='#5c5f77', muted='#6c6f85', faint='#8c8fa1',
                      accent='#8839ef', ok='#40a02b', warn='#df8e1d', danger='#d20f39',
                      covers=['#1e66f5', '#8839ef', '#179299', '#40a02b', '#fe640b'],
                      chart=['#1e66f5', '#8839ef', '#179299', '#40a02b', '#fe640b', '#d20f39', '#ea76cb', '#df8e1d']),
        'dark': dict(paper='#1e1e2e', shell='#11111b', sidebar='#181825', surface='#313244', field='#181825',
                     line='#313244', ink='#cdd6f4', soft='#bac2de', muted='#a6adc8', faint='#7f849c',
                     accent='#cba6f7', ok='#a6e3a1', warn='#f9e2af', danger='#f38ba8',
                     covers=['#89b4fa', '#cba6f7', '#94e2d5', '#a6e3a1', '#fab387'],
                     chart=['#89b4fa', '#cba6f7', '#94e2d5', '#a6e3a1', '#fab387', '#f38ba8', '#f5c2e7', '#f9e2af']),
    },
    'everforest': {
        'name': 'Everforest',
        'source': 'sainnhe/everforest (MIT): medium light and dark',
        # Light is cooled a little from upstream's cream, toward the misty green of the forest.
        'light': dict(paper='#f7f6ed', shell='#e6eadf', sidebar='#eceee4', surface='#eef0e6', field='#fcfcf7',
                      line='#dadfd1', ink='#5c6a72', soft='#6c7a80', muted='#829181', faint='#939f91',
                      accent='#35a77c', ok='#8da101', warn='#dfa000', danger='#f85552',
                      covers=['#3a94c5', '#df69ba', '#35a77c', '#8da101', '#f57d26'],
                      chart=['#35a77c', '#3a94c5', '#df69ba', '#8da101', '#f57d26', '#f85552', '#dfa000', '#829181']),
        'dark': dict(paper='#2d353b', shell='#232a2e', sidebar='#232a2e', surface='#3d484d', field='#343f44',
                     line='#3d484d', ink='#d3c6aa', soft='#c5b99f', muted='#9da9a0', faint='#859289',
                     accent='#a7c080', ok='#83c092', warn='#dbbc7f', danger='#e67e80',
                     covers=['#7fbbb3', '#d699b6', '#83c092', '#a7c080', '#e69875'],
                     chart=['#a7c080', '#7fbbb3', '#d699b6', '#83c092', '#e69875', '#e67e80', '#dbbc7f', '#9da9a0']),
        # A misty spruce forest, after the Everforest wallpaper in Omarchy, behind the sidebar and
        # along the top of the window. Daytime in light mode, with no motion. Winter night in dark
        # mode: snow on the trees, soft snow falling, and snow slowly settling on the shelf and buttons.
        'extra': SCENERY + '''
{root} .sidebar{text-shadow:0 0 3px var(--shell),0 0 9px var(--shell)}
{root} body{background-image:var(--art-forest);background-size:cover;background-position:left bottom;background-attachment:fixed}
{root} .window-bar::before,{root} .sidebar::before,{root} .sidebar::after,{root}[data-theme="light"] body::before,{root}[data-theme="light"] body::after,{root}[data-theme="light"] .window-bar::after{display:none}
''' + falling('{root}[data-theme="dark"] body::before', 'var(--art-snow-near)', 260, 300, 13, .95, sway=12)
    + falling('{root}[data-theme="dark"] body::after', 'var(--art-snow-far)', 180, 220, 24, .8, sway=6) + '''
{root}[data-theme="dark"] .window-bar::after{left:calc(var(--fx-rail) + 19px);right:29px;top:var(--bar-height);height:12px;z-index:-1;background:var(--art-drift) repeat-x;background-size:160px 12px;transform-origin:50% 100%;animation:fx-settle 480s ease-out forwards}
{root}[data-theme="dark"] .window-bar .button,{root}[data-theme="dark"] .theme-toggle,{root}[data-theme="dark"] .sidebar .nav.active{box-shadow:inset 0 2px 0 #ece6d6b3}
@keyframes fx-settle{from{transform:scaleY(.35)}to{transform:none}}
@media(prefers-reduced-motion:reduce){{root} body::before,{root} body::after{display:none}{root}[data-theme="dark"] .window-bar::after{animation:none}}
''',
    },
    'newsprint': {
        'name': 'Newsprint',
        'source': 'KoShelf: a black-and-white newspaper and its night edition',
        'light': dict(paper='#f1f0ec', shell='#e4e3de', sidebar='#e9e8e3', surface='#f7f6f3', field='#ffffff',
                      line='#bdbbb5', ink='#141414', soft='#3a3937', muted='#6b6966', faint='#8c8a86',
                      accent='#141414', ok='#4a4a48', warn='#5c5a55', danger='#8f1d1d',
                      covers=['#2b2b2b', '#555555', '#777777', '#3c3c3c', '#666666'],
                      chart=['#1a1a1a', '#8a8a8a', '#4a4a4a', '#b5b5b5', '#333333', '#a0a0a0', '#666666', '#cfcfcf'],
                      overrides={'shadow-faint': 'transparent', 'shadow': 'transparent', 'shadow-strong': 'transparent',
                                 'glow': 'transparent', 'sheen': 'transparent', 'backdrop': '#14141466'}),
        'dark': dict(paper='#171716', shell='#0f0f0e', sidebar='#121211', surface='#1f1f1e', field='#1b1b1a',
                     line='#3a3a38', ink='#e9e7e2', soft='#c4c2bd', muted='#8f8d89', faint='#6f6d6a',
                     accent='#e9e7e2', ok='#a8a6a1', warn='#b0aea8', danger='#e07a7a',
                     covers=['#d8d8d8', '#9a9a9a', '#bcbcbc', '#7a7a7a', '#e6e6e6'],
                     chart=['#e6e6e6', '#7a7a7a', '#b8b8b8', '#4f4f4f', '#d0d0d0', '#626262', '#9c9c9c', '#3e3e3e'],
                     overrides={'shadow-faint': 'transparent', 'shadow': 'transparent', 'shadow-strong': 'transparent',
                                'glow': 'transparent', 'sheen': 'transparent', 'backdrop': '#000000a6'}),
        'fonts': {'serif': '"Bodoni 72",Didot,"Bodoni MT","Times New Roman",serif',
                  'sans': '"Helvetica Neue",Helvetica,Arial,sans-serif'},
        # Square corners and ink rules, a masthead double rule under the heading, and covers
        # printed in grayscale that come back to color under the pointer.
        'extra': '''
{root} *{round}{border-radius:0!important}
{root} main,{root} dialog,{root} .storage-panel,{root} .button,{root} .theme-toggle{border-color:var(--ink)}
{root} main header{border-bottom:3px double var(--ink);margin-bottom:28px}
{root} h1{font-weight:700;letter-spacing:-1.5px}
{root} .cover img{filter:grayscale(1) contrast(1.08);transition:filter .5s}
{root} .book:hover .cover img,{root} .book:focus-visible .cover img{filter:none}
{root} .brand-icon{filter:grayscale(1) contrast(1.1)}
.theme-preview i[data-palette="newsprint"]::before{font-family:"Bodoni 72",Didot,serif;font-weight:700}
''',
    },
    'matrix': {
        'name': 'Matrix',
        'source': 'KoShelf: a phosphor terminal, and green-bar printer paper in light mode',
        'light': dict(paper='#f3f8f0', shell='#e3ecdf', sidebar='#eaf2e6', surface='#f8fbf6', field='#ffffff',
                      line='#b9d3bc', ink='#0c3a1c', soft='#1f5631', muted='#3f7350', faint='#6c967a',
                      accent='#0a7d2c', ok='#2f8f46', warn='#8a7a00', danger='#b3261e',
                      covers=['#0a7d2c', '#126b6b', '#3a6b16', '#1d5c3a', '#5a6b00'],
                      chart=['#0a7d2c', '#7fbf8e', '#126b6b', '#b5d8bc', '#3a6b16', '#4fa36a', '#1d5c3a', '#9fcfa9'],
                      overrides={'shadow-faint': '#0a7d2c1a', 'shadow': '#0a7d2c29', 'shadow-strong': '#0a7d2c3d'}),
        'dark': dict(paper='#030a05', shell='#010502', sidebar='#020703', surface='#07140a', field='#041008',
                     line='#0f3a1a', ink='#a8ffbf', soft='#7fe39a', muted='#4fae6a', faint='#3a8752',
                     accent='#00ff41', ok='#39ff6a', warn='#d4ff3a', danger='#ff4f6d',
                     covers=['#00ff41', '#00c853', '#39ff6a', '#1aff8c', '#00b33a'],
                     chart=['#00ff41', '#0b8f2e', '#7dffa0', '#00b33a', '#c2ffd2', '#055c1f', '#3dff79', '#1fd15a'],
                     overrides={'shadow-faint': '#00ff4126', 'shadow': '#00ff4140', 'shadow-strong': '#00ff4152',
                                'glow': '#00ff4140', 'sheen': '#00ff410f', 'backdrop': '#000000c0'}),
        'fonts': {'serif': 'var(--mono)', 'sans': 'var(--mono)'},
        # Monospace throughout and green-monochrome covers. Dark mode glows, with scanlines and a
        # cursor after the heading; light mode is green-bar printer paper.
        'extra': '''
{root} *{round}{border-radius:0!important}
{root} h1{letter-spacing:-2px;font-weight:600}
{root} .brand-icon{filter:grayscale(1) sepia(1) hue-rotate(70deg) saturate(2.4)}
{root} .toolbar{flex-wrap:wrap}
{root} .search{min-width:220px}
{root} .cover img{filter:grayscale(1) sepia(1) hue-rotate(70deg) saturate(2.4) brightness(.92);transition:filter .5s}
{root} .book:hover .cover img,{root} .book:focus-visible .cover img{filter:none}
{root}[data-theme="light"] main{background-image:repeating-linear-gradient(180deg,transparent 0 54px,#0a7d2c0d 54px 108px);background-attachment:local}
{root}[data-theme="dark"] h1,{root}[data-theme="dark"] .brand,{root}[data-theme="dark"] .storage-size,{root}[data-theme="dark"] dialog h2,{root}[data-theme="dark"] .waiting-copy h1{text-shadow:0 0 6px #00ff4199,0 0 18px #00ff4140}
{root}[data-theme="dark"] .primary,{root}[data-theme="dark"] .button:hover{box-shadow:0 0 12px #00ff4159}
{root}[data-theme="dark"] body::after{content:'';position:fixed;inset:0;z-index:50;pointer-events:none;background:repeating-linear-gradient(180deg,transparent 0 2px,#00000038 2px 3px)}
{root}[data-theme="dark"] h1#heading::after{content:'';display:inline-block;width:.45em;height:.8em;margin-left:.12em;background:var(--green);box-shadow:0 0 10px var(--green);animation:koshelf-cursor 1.1s steps(1) infinite}
@keyframes koshelf-cursor{50%{opacity:0}}
@media(prefers-reduced-motion:reduce){{root} h1#heading::after{animation:none}}
.theme-preview i[data-palette="matrix"]::before{font-family:var(--mono)}
''',
    },
}


def rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, amount):
    """`amount` of b mixed into a, in sRGB."""
    return '#' + ''.join(f'{round(x + (y - x) * amount):02x}' for x, y in zip(rgb(a), rgb(b)))


def luminance(color):
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(value) for value in rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def legible(color, background, target, toward):
    """The color, moved toward `toward` just enough to reach `target` contrast with the background."""
    for step in range(21):
        candidate = mix(color, toward, step / 20)
        if contrast(candidate, background) >= target:
            return candidate
    return toward


def tokens(p, dark):
    paper, ink = p['paper'], p['ink']
    # Some upstream light palettes are paler than KoShelf's text needs, so colors used as
    # text are darkened (or lightened, in dark variants) until they read clearly.
    text = '#ffffff' if dark else '#000000'
    p = dict(p, soft=legible(p['soft'], paper, 4.5, text), muted=legible(p['muted'], paper, 3.5, text),
             faint=legible(p['faint'], paper, 2.6, text), accent=legible(p['accent'], paper, 3.5, text),
             danger=legible(p['danger'], paper, 4.5, text))
    accent = p['accent']
    # Text on the accent: whichever of the paper or white reads better.
    on_accent = max((paper, '#ffffff', '#000000'), key=lambda color: contrast(color, accent))
    shade = '#000000'
    values = {
        'paper': paper, 'shell': p['shell'], 'sidebar': p['sidebar'], 'surface': p['surface'], 'field': p['field'],
        'panel': mix(paper, ink, .035), 'panel-warm': mix(paper, p['warn'], .06),
        'hover': mix(paper, ink, .07), 'selected': mix(paper, accent, .16), 'selected-strong': mix(paper, accent, .3),
        'placeholder': mix(paper, ink, .09),
        'line': p['line'], 'line-strong': mix(p['line'], ink, .12), 'line-heavy': mix(paper, ink, .35),
        'ink': ink, 'ink-soft': p['soft'], 'muted': p['muted'], 'faint': p['faint'],
        'warm-ink': mix(p['soft'], p['warn'], .25),
        'green': accent, 'accent-hover': mix(accent, '#ffffff' if dark else shade, .18),
        'accent-ink': mix(accent, ink, .35), 'accent-mid': p['ok'], 'accent-soft': mix(p['ok'], paper, .3),
        'on-accent': on_accent, 'focus': mix(accent, paper, .25),
        'warn-bg': mix(paper, p['warn'], .12), 'warn-line': mix(paper, p['warn'], .32), 'warn-mid': p['warn'],
        'warn-ink': legible(mix(p['warn'], ink, .2 if dark else .4), mix(paper, p['warn'], .12), 4.5, text),
        'danger': p['danger'],
        'sheen': '#ffffff0b' if dark else '#ffffff23', 'glow': accent + '1c',
        'shadow-faint': '#00000033' if dark else ink + '1a', 'shadow': '#00000047' if dark else ink + '24',
        'shadow-strong': '#00000066' if dark else ink + '33', 'backdrop': '#000000a6' if dark else ink + '55',
    }
    for n, hue in enumerate(p['covers'], 1):
        values[f'cover-{n}-bg'] = mix(paper, hue, .3 if dark else .25)
        values[f'cover-{n}-ink'] = legible(mix(ink, hue, .25) if dark else mix(hue, ink, .45),
                                           values[f'cover-{n}-bg'], 4.5, text)
    for n, color in enumerate(p['chart'], 1):
        values[f'chart-{n}'] = color
    values['chart-none'] = mix(paper, ink, .18)
    values.update(p.get('overrides', {}))
    return values


def art(key, mode, values):
    """The theme's SVGs for this mode as --art-<name> data URLs, colored with the mode's tokens."""
    variables = {}
    for path in sorted([*(ART / key).glob('*.svg'), *(ART / key / mode).glob('*.svg')]):
        def token(name):
            if name not in values:
                raise SystemExit(f'{path.name}: unknown token {name}')
            return values[name]

        def color(match):
            # {{name}} is a token; {{mix(a,b,t)}} mixes two tokens, or a token and a #hex color.
            mixed = re.fullmatch(r'mix\(([\w#-]+),([\w#-]+),([\d.]+)\)', match.group(1))
            if mixed:
                a, b, amount = mixed.groups()
                return mix(*(c if c.startswith('#') else token(c) for c in (a, b)), float(amount))
            return token(match.group(1))
        svg = re.sub(r'\{\{([^{}]+)\}\}', color, path.read_text())
        svg = re.sub(r'<!--.*?-->', '', svg, flags=re.S)
        svg = re.sub(r'>\s+<', '><', ' '.join(svg.split())).replace('"', "'")
        variables[f'art-{path.stem}'] = 'url("data:image/svg+xml,' + urllib.parse.quote(svg, safe=" '/:=;,.-()") + '")'
    return variables


def css():
    lines = ['/* Generated by scripts/build-themes.py. Edit the palettes there, not this file. */']
    for key, theme in THEMES.items():
        lines.append(f'/* {theme["name"]}: {theme["source"]} */')
        for mode in ('light', 'dark'):
            values = tokens(theme[mode], mode == 'dark')
            values.update(art(key, mode, values))
            body = ';'.join(f'--{name}:{value}' for name, value in values.items())
            lines.append(f'[data-palette="{key}"][data-theme="{mode}"]{{color-scheme:{mode};{body}}}')
        root = f':root[data-palette="{key}"]'
        if 'fonts' in theme:
            lines.append(root + '{' + ';'.join(f'--{name}:{value}' for name, value in theme['fonts'].items()) + '}')
        if 'extra' in theme:
            lines.append(theme['extra'].strip().replace('{root}', root).replace('{round}', ROUND))
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    OUTPUT.write_text(css())
    print(f'Wrote {OUTPUT}')
