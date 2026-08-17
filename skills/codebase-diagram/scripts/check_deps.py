#!/usr/bin/env python3
"""
check_deps.py — report what this skill needs and what is missing, with the
command that fixes each gap. Run it before the first build in a repo.

Hard requirements: Graphviz `dot` (layout) and defusedxml (reading .drawio).
Soft: a rasteriser, needed only for PNG. SVG is always produced without one.
Exit status is 1 when a hard requirement is missing, 0 otherwise.
"""
import importlib, shutil, subprocess, sys

HINT = {
    'dot': 'micromamba install -n py311 graphviz   (or: apt install graphviz)',
    'defusedxml': 'micromamba install -n py311 defusedxml   (or: pip install defusedxml)',
    'raster': 'pip install cairosvg   (or install librsvg / inkscape / imagemagick)',
}
RASTERISERS = ('rsvg-convert', 'inkscape', 'convert')


def have_module(name):
    assert name, 'module name required'
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


def dot_version():
    """Graphviz version string, or '' when `dot` is absent or broken."""
    if not shutil.which('dot'):
        return ''
    r = subprocess.run(['dot', '-V'], capture_output=True, text=True)
    return (r.stderr or r.stdout).strip()


def rasteriser():
    """Name of the first available SVG->PNG rasteriser, or ''."""
    if have_module('cairosvg'):
        return 'cairosvg'
    return next((t for t in RASTERISERS if shutil.which(t)), '')


def main():
    rows = []
    version = dot_version()
    rows.append(('Graphviz dot', bool(version), version or HINT['dot'], True))
    rows.append(('defusedxml', have_module('defusedxml'), 'importable' if have_module('defusedxml') else HINT['defusedxml'], True))
    tool = rasteriser()
    rows.append(('PNG rasteriser', bool(tool), tool or HINT['raster'], False))

    missing_hard = False
    for name, ok, detail, hard in rows:
        mark = 'ok  ' if ok else ('MISSING' if hard else 'absent ')
        print(f'{mark} {name}: {detail}')
        missing_hard = missing_hard or (hard and not ok)
    if missing_hard:
        print('\nInstall the missing requirements above, then re-run this check.')
    elif not tool:
        print('\nPNG will be skipped; SVG and .drawio still build.')
    print(f'python: {sys.version.split()[0]} ({sys.executable})')
    return 1 if missing_hard else 0


if __name__ == '__main__':
    sys.exit(main())
