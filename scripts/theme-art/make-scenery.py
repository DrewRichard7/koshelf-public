#!/usr/bin/env python3
"""Draw the procedural scenery SVGs: Everforest's misty spruce forest and snow.

The drawings are deterministic (fixed random seeds), so running this again gives the same files.
Colors are {{token}} or {{mix(a,b,t)}} placeholders that build-themes.py fills per mode. After
running this, run build-themes.py. The output is CC0, like the rest of KoShelf's own art.

    python3 scripts/theme-art/make-scenery.py
"""
import math
from pathlib import Path
import random

HERE = Path(__file__).resolve().parent


def pt(x, y):
    return f'{round(x)} {round(y)}'


# ---- Everforest: a misty spruce forest on ridges that rise to the right ----

def spruce(rng, x, base, height, snow):
    """A spruce's outline (one closed subpath), and slivers of snow on its tiers if `snow`."""
    tiers = max(3, min(7, int(height / 18)))
    width = height * rng.uniform(.28, .36)
    step = height / tiers
    top = base - height
    right, left, caps = [], [], []
    inner = (0, top)
    for k in range(1, tiers + 1):
        y = top + step * k * .94
        reach = width * (k / tiers) ** .85 * rng.uniform(.82, 1.12)
        outer = (reach, y)
        right.append(outer)
        left.append((-reach * rng.uniform(.9, 1.1), y + rng.uniform(-1.5, 1.5)))
        if snow:
            thick = max(1.1, step * .22)
            for side, o in ((1, outer), (-1, left[-1])):
                start = (inner[0] * side, inner[1])
                caps.append([start, o, (start[0] + side * thick * .3, start[1] + thick)])
        inner = (reach * .32, y - step * .3)
        if k < tiers:
            right.append(inner)
            left.append((-inner[0], inner[1]))
    trunk = width * .08
    outline = [(0, top), *right, (trunk, base + 4), (-trunk, base + 4), *reversed(left)]
    path = 'M' + ' '.join(pt(x + dx, y) for dx, y in outline) + 'Z'
    snow_path = ''.join('M' + ' '.join(pt(x + dx, y) for dx, y in cap) + 'Z' for cap in caps)
    return path, snow_path


def ridge_y(x, base, slope, phase):
    return base - slope * x + 18 * math.sin(x / 140 + phase) + 9 * math.sin(x / 47 + phase * 2)


def forest(snow):
    rng = random.Random(7)
    width, height = 1600, 1000
    # Far to near: ridge baseline, rise to the right, tree height, spacing, fill, snow opacity.
    if snow:
        fills = ['{{mix(shell,ink,.12)}}', '{{mix(shell,ink,.06)}}', '{{mix(shell,#000000,.18)}}', '{{mix(shell,#000000,.34)}}']
    else:
        fills = ['{{mix(shell,ink,.07)}}', '{{mix(shell,ink,.11)}}', '{{mix(shell,ink,.15)}}', '{{mix(shell,ink,.19)}}']
    # Only the window margins show the forest: the sidebar on the left and a strip along the top.
    # So only the far ridge, which rises into that strip, spans the whole width.
    layers = [(250, .11, 46, 13, fills[0], .35, width), (470, .08, 70, 18, fills[1], .4, 560),
              (690, .05, 100, 26, fills[2], .42, 560), (900, .03, 140, 36, fills[3], .45, 560)]
    parts = ['<defs><linearGradient id="fog" x1="0" y1="0" x2="0" y2="1">'
             '<stop offset="0" stop-color="{{shell}}" stop-opacity="0"/>'
             '<stop offset=".6" stop-color="{{shell}}" stop-opacity=".75"/>'
             '<stop offset="1" stop-color="{{shell}}" stop-opacity="0"/></linearGradient>'
             '<radialGradient id="mist"><stop offset="0" stop-color="{{shell}}" stop-opacity=".8"/>'
             '<stop offset="1" stop-color="{{shell}}" stop-opacity="0"/></radialGradient></defs>']
    for index, (base, slope, tree, spacing, fill, snow_opacity, reach) in enumerate(layers):
        phase = index * 1.7
        trees, caps = [], []
        x = -20
        while x < reach + 40:
            y = ridge_y(x, base, slope, phase) + rng.uniform(-6, 10)
            # The far ridge is too small to show snow on its branches; its pale color reads as snow.
            outline, cap = spruce(rng, x, y, tree * rng.uniform(.7, 1.25), snow and index > 0)
            trees.append(outline)
            caps.append(cap)
            x += spacing * rng.uniform(.6, 1.3)
        ground = 'M' + ' '.join(pt(x, ridge_y(x, base, slope, phase) + 8) for x in range(-20, reach + 41, 40))
        ground += f' {reach + 40} {height}L-20 {height}Z'
        parts.append(f'<path fill="{fill}" d="{"".join(trees)}{ground}"/>')
        if snow and index > 0:
            parts.append(f'<path fill="{{{{mix(ink,#ffffff,.3)}}}}" fill-opacity="{snow_opacity}" d="{"".join(caps)}"/>')
        if index < len(layers) - 1:
            # Fog settling below this ridge, and a few wisps drifting over the next.
            low = ridge_y(width / 2, base, slope, phase)
            parts.append(f'<rect x="0" y="{round(low - 60)}" width="{width}" height="{round(tree * 2.4)}" fill="url(#fog)"/>')
            for _ in range(2):
                cx, cy = rng.uniform(0, 500), low + rng.uniform(10, 80)
                parts.append(f'<ellipse cx="{round(cx)}" cy="{round(cy)}" rx="{round(rng.uniform(120, 260))}" '
                             f'ry="{round(rng.uniform(18, 36))}" fill="url(#mist)"/>')
    kind = 'with snow on the trees' if snow else 'in daylight'
    return (f'<!-- Misty spruce forest {kind}. Drawn by make-scenery.py for KoShelf (CC0). -->\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" preserveAspectRatio="xMinYMax slice">'
            + ''.join(parts) + '</svg>\n')


def snowfall(seed, width, height, count, radius):
    """A tile of soft snowflakes with blurred edges."""
    rng = random.Random(seed)
    flakes = ''.join(f'<circle cx="{rng.uniform(4, width - 4):.0f}" cy="{rng.uniform(4, height - 4):.0f}" '
                     f'r="{rng.uniform(*radius):.1f}"/>' for _ in range(count))
    return (f'<!-- Snowflakes, tiled. Drawn by make-scenery.py for KoShelf (CC0). -->\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            '<defs><radialGradient id="flake"><stop offset="0" stop-color="{{mix(ink,#ffffff,.6)}}"/>'
            '<stop offset=".45" stop-color="{{mix(ink,#ffffff,.6)}}" stop-opacity=".85"/>'
            '<stop offset="1" stop-color="{{mix(ink,#ffffff,.6)}}" stop-opacity="0"/></radialGradient></defs>'
            f'<g fill="url(#flake)">{flakes}</g></svg>\n')


if __name__ == '__main__':
    files = {
        'everforest/light/forest.svg': forest(snow=False),
        'everforest/dark/forest.svg': forest(snow=True),
        'everforest/dark/snow-near.svg': snowfall(3, 260, 300, 9, (2.2, 4.2)),
        'everforest/dark/snow-far.svg': snowfall(5, 180, 220, 18, (.9, 2.1)),
    }
    for name, svg in files.items():
        (HERE / name).write_text(svg)
        print(f'{name}: {len(svg)} bytes')
