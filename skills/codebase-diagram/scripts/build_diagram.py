#!/usr/bin/env python3
"""
build_diagram.py — turn a graph JSON into an editable .drawio, laying the nodes
out with Graphviz `dot` and baking the resulting positions and routed edge
waypoints into the file. Optionally renders SVG/PNG in the same run.

The graph JSON holds three keys: `config` (bands, colours, repo linking),
`nodes` and `edges`. See reference/graph-schema.md for the field list. Every
per-diagram choice lives in that file, so this script is the single generator
for any repo's diagram; drawio_common.py holds the pixel emission it shares
with any other generator.

Usage:
    python build_diagram.py graph.json                 # -> graph.drawio
    python build_diagram.py graph.json -o out.drawio
    python build_diagram.py graph.json --render        # + .svg and .png
    python build_diagram.py graph.json --check         # validate only
Requires Graphviz `dot` on PATH.
"""
import argparse, json, os, re, subprocess, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drawio_common import (run_dot, transforms, decl, emit_node, emit_edges,
                           wrap_mxfile, esc, node_rect)

# fill, stroke, font. A band names one of these and gets both its node colour
# and its band-label colour from it, so the two can never drift apart.
PALETTE = {
    'slate':    ('#dfe7ef', '#6b7f96', '#1b2733'),
    'teal':     ('#cfe8e6', '#4f9b95', '#163a37'),
    'green':    ('#cfe6cf', '#5a9367', '#1e3a24'),
    'peach':    ('#f6d9c0', '#c17a3a', '#5a3410'),
    'lavender': ('#e6d6f2', '#8a5fb0', '#3d2757'),
    'grey':     ('#e3e8ef', '#5b6675', '#1b2733'),
    'steel':    ('#e6ecf3', '#8493a4', '#1b2733'),
}

SCRIPT_BASE = 'rounded=1;whiteSpace=wrap;html=1;fontSize=11;'
TYPE_STYLE = {
    'ext':    'rounded=1;whiteSpace=wrap;html=1;arcSize=45;fillColor=#2f7dc4;strokeColor=#1c4f80;fontColor=#ffffff;fontSize=11;',
    'danger': 'rounded=1;whiteSpace=wrap;html=1;arcSize=45;fillColor=#c0392b;strokeColor=#7d2318;fontColor=#ffffff;fontSize=11;',
    'data':   'shape=cylinder;whiteSpace=wrap;html=1;fillColor=#e6ecf3;strokeColor=#8493a4;fontColor=#1b2733;fontSize=11;',
    'note':   'shape=note;whiteSpace=wrap;html=1;size=14;fillColor=#fdf3cf;strokeColor=#c9a227;fontColor=#5b4a00;fontSize=12;dashed=1;',
    'proxy':  'rounded=1;whiteSpace=wrap;html=1;fillColor=#eef2f7;strokeColor=#8493a4;fontColor=#5b6675;fontSize=9;dashed=1;',
}
NODE_TYPES = set(TYPE_STYLE) | {'script'}

EDGE_BASE = ('edgeStyle=none;curved=1;html=1;endArrow=block;endFill=1;'
             'strokeColor=#9aa6b3;fontSize=9;fontColor=#5b6675;')
DEFAULT_EDGE_KINDS = {
    '':        {},
    'flow':    {'color': '#2e8b57'},
    'handoff': {'color': '#2e8b57'},
    'loader':  {'color': '#3d72b4', 'dashed': True},
    'cache':   {'color': '#b98a1e', 'dashed': True},
    'store':   {'color': '#b98a1e', 'dashed': True},
    'mask':    {'color': '#1e7a3a', 'width': 2},
    'boot':    {'color': '#c17a3a', 'dashed': True},
    'nav':     {'color': '#8493a4', 'dashed': True},
    'proxy':   {'color': '#8493a4', 'dashed': True},
    'danger':  {'color': '#c0392b', 'dashed': True},
}

LINKABLE = re.compile(r'([\w.-]+\.(?:jsonl|json|csv|pdf|pkl|db|ya?ml|toml|txt|md))')


def fail(msg):
    """Stop on a problem in the graph file, not in this program."""
    raise SystemExit(f'graph error: {msg}')


def edge_style(spec):
    """Build one edge's draw.io style from a kind spec dict."""
    assert isinstance(spec, dict), spec
    out = EDGE_BASE
    if spec.get('color'):
        out += f'strokeColor={spec["color"]};'
    if spec.get('dashed'):
        out += 'dashed=1;'
    if spec.get('width'):
        out += f'strokeWidth={spec["width"]};'
    return out


def git(root, *args):
    """Run a read-only git command in `root`; '' when git or the repo is absent."""
    assert args, 'no git subcommand given'
    try:
        r = subprocess.run(['git', '-C', root, *args], capture_output=True, text=True)
    except FileNotFoundError:
        return ''
    return r.stdout.strip() if r.returncode == 0 else ''


class RepoLinks:
    """Turns a node's `file` (and an edge's label) into a GitHub URL.

    In `tracked` mode (the default) git is the authority rather than the
    filesystem: an ignored path exists on this machine but not on GitHub, and a
    per-user template like `database/<id>/token.bin` never exists anywhere.
    Both must stay unlinked. `trust` mode links any path that is not obviously
    a template, for a graph JSON kept outside the repo it describes, where
    there is no checkout to ask."""

    TEMPLATE_CHARS = '{[<\''

    def __init__(self, cfg, json_dir):
        gh = cfg.get('github', {})
        self.mode = gh.get('link_mode', 'tracked')
        if self.mode not in ('tracked', 'trust'):
            fail(f'github.link_mode {self.mode!r} must be "tracked" or "trust"')
        self.root = os.path.abspath(os.path.join(json_dir, gh['root'])) if gh.get('root') \
            else (git(json_dir, 'rev-parse', '--show-toplevel') or json_dir)
        self.base = gh.get('base') or self._remote_base()
        self.branch = gh.get('branch') or git(self.root, 'rev-parse', '--abbrev-ref', 'HEAD') or 'main'
        listing = git(self.root, 'ls-files') if self.mode == 'tracked' else ''
        self.tracked = set(listing.split('\n')) if listing else set()
        self.file_index = self._index(gh.get('label_link_dirs', []))
        assert isinstance(self.tracked, set)

    def _remote_base(self):
        """https URL for origin, from either the ssh or https remote form."""
        url = git(self.root, 'remote', 'get-url', 'origin')
        if not url:
            return ''
        m = re.match(r'(?:git@([^:]+):|https?://(?:[^@]+@)?([^/]+)/)(.+?)(?:\.git)?$', url)
        return f'https://{m.group(1) or m.group(2)}/{m.group(3)}' if m else ''

    def _index(self, dirs):
        """filename -> repo path, for auto-linking edge labels that name a data
        file. Earlier directories win, so a demo artifact beats a generic copy."""
        index = {}
        for d in dirs:
            prefix = d.rstrip('/') + '/'
            if self.mode == 'tracked':
                names = [p[len(prefix):] for p in self.tracked
                         if p.startswith(prefix) and '/' not in p[len(prefix):]]
            else:
                full = os.path.join(self.root, prefix)
                names = sorted(os.listdir(full)) if os.path.isdir(full) else []
            for name in names:
                index.setdefault(name, prefix + name)
        return index

    def _url(self, path):
        trimmed = path.rstrip('/')
        kind = 'tree' if path.endswith('/') or '.' not in os.path.basename(trimmed) else 'blob'
        return f'{self.base}/{kind}/{self.branch}/{trimmed}'

    def node(self, path):
        """URL for a checked-in repo path, or '' for anything git does not track."""
        trimmed = (path or '').rstrip('/')
        if not self.base or not trimmed:
            return ''
        if self.mode == 'trust':
            return '' if any(c in path for c in self.TEMPLATE_CHARS) else self._url(path)
        if trimmed in self.tracked:
            return self._url(path)
        if path.endswith('/') and any(p.startswith(trimmed + '/') for p in self.tracked):
            return self._url(path)
        return ''

    def label(self, label):
        """URL when an edge label names one indexed data file, else ''. Control
        flow labels ('startup', 'prediction') and variable names have no target."""
        if not self.base or not label:
            return ''
        for m in LINKABLE.finditer(label):
            path = self.file_index.get(m.group(1))
            if path:
                return f'{self.base}/blob/{self.branch}/{path}'
        return ''


class Diagram:
    """One graph JSON rendered to one .drawio."""

    def __init__(self, path):
        self.graph = json.load(open(path))
        self.cfg = self.graph.get('config', {})
        self.nodes = self.graph.get('nodes', [])
        self.edges = self.graph.get('edges', [])
        self.bands = self.cfg.get('bands', [])
        self.band_keys = [b['key'] for b in self.bands]
        self.rank_of = {k: i for i, k in enumerate(self.band_keys)}
        self.grp = {n['id']: n.get('grp', '') for n in self.nodes}
        self.kinds = dict(DEFAULT_EDGE_KINDS, **self.cfg.get('edge_kinds', {}))
        self.links = RepoLinks(self.cfg, os.path.dirname(os.path.abspath(path)))
        assert self.band_keys or not self.nodes, 'config.bands is required'

    # ---------- validation --------------------------------------------------
    def validate(self):
        """Every check that catches an authoring mistake before dot runs."""
        ids = [n['id'] for n in self.nodes]
        dupes = [i for i, c in Counter(ids).items() if c > 1]
        if dupes:
            fail(f'duplicate node ids: {sorted(dupes)}')
        known = set(ids)
        allowed_grp = set(self.band_keys) | {'ext'}
        for n in self.nodes:
            if n.get('type', 'script') not in NODE_TYPES:
                fail(f'node {n["id"]}: unknown type {n.get("type")!r}, expected one of {sorted(NODE_TYPES)}')
            if n.get('grp', '') not in allowed_grp:
                fail(f'node {n["id"]}: grp {n.get("grp")!r} is not a band key or "ext"; bands are {self.band_keys}')
            if not n.get('lbl'):
                fail(f'node {n["id"]}: lbl is required')
        for i, e in enumerate(self.edges):
            for end in ('from', 'to'):
                if e.get(end) not in known:
                    fail(f'edge {i} ({e.get("from")}->{e.get("to")}): {end} is not a node id')
            e['kind'] = e.get('kind') or ''
            if e['kind'] not in self.kinds:
                fail(f'edge {i}: unknown kind {e.get("kind")!r}; add it to config.edge_kinds '
                     f'or use one of {sorted(k for k in self.kinds if k)}')
        for b in self.bands:
            if b.get('palette', 'slate') not in PALETTE:
                fail(f'band {b["key"]}: unknown palette {b.get("palette")!r}, expected one of {sorted(PALETTE)}')
        return len(self.nodes), len(self.edges)

    # ---------- preparation -------------------------------------------------
    def apply_links(self):
        """Fill in node and edge links; an explicit `link` in the JSON wins."""
        for n in self.nodes:
            if not n.get('link'):
                n['link'] = self.links.node(n.get('file', '')) or None
        for e in self.edges:
            if not e.get('link'):
                e['link'] = self.links.label(e.get('label', '')) or None
        assert all('link' in n for n in self.nodes)

    def size_nodes(self):
        """Label-driven sizing, so Graphviz reserves each box's real footprint.
        The full path goes under the short name: a diagram of a repo should say
        where each thing lives."""
        show_paths = self.cfg.get('show_paths', True)
        for n in self.nodes:
            if show_paths and n.get('file'):
                n['lbl'] = n['lbl'] + '\n' + n['file']
            if 'w' in n:                     # honour an explicit size
                continue
            lines = n['lbl'].split('\n')
            n['w'] = max(90, int(max(len(x) for x in lines) * 6.6) + 24) + (20 if n.get('robot') else 0)
            n['h'] = len(lines) * 15 + (26 if n.get('type') == 'data' else 20)

    def node_style(self, n):
        if n.get('type', 'script') != 'script':
            return TYPE_STYLE[n['type']]
        band = next((b for b in self.bands if b['key'] == n['grp']), self.bands[0])
        fill, stroke, font = PALETTE[band.get('palette', 'slate')]
        return SCRIPT_BASE + f'fillColor={fill};strokeColor={stroke};fontColor={font};'

    # ---------- layout ------------------------------------------------------
    def constrains(self, e):
        """Whether an edge may set layer rank.

        Two classes must not. An external service feeding several bands drags
        them together if it ranks. And any edge running against the band order
        contradicts the funnel below; left constrained, dot resolves the
        contradiction by reversing whichever edge it likes, which throws nodes
        to the wrong end of the page. The bands are declared by config; a
        return arrow is drawn, not obeyed."""
        if self.grp.get(e['from']) == 'ext' and self.outdeg[e['from']] > 1:
            return False
        if not self.cfg.get('relax_backward_edges', True):
            return True
        a, b = self.rank_of.get(self.grp.get(e['from'])), self.rank_of.get(self.grp.get(e['to']))
        return a is None or b is None or b >= a

    def dot_source(self):
        """DOT for the layout pass: real edges, plus one invisible funnel node
        per band gap so every node of band k ranks above every node of k+1.

        Those funnel edges carry weight=0. Rank assignment still obeys them,
        but the x-coordinate pass ignores them, and that pass is where the
        damage was: a band's ten edges converging on one invisible point
        stacked the whole band into a column under it. Zero weight leaves
        horizontal placement to the edges actually drawn, which is what makes a
        caller sit next to what it calls."""
        self.outdeg = Counter(e['from'] for e in self.edges)
        dot = ['digraph P{', f'rankdir={self.cfg.get("rankdir", "BT")};', 'splines=polyline;',
               f'nodesep={self.cfg.get("nodesep", 0.45)};', f'ranksep={self.cfg.get("ranksep", 0.85)};',
               'node[shape=box,fixedsize=true];']
        dot += [decl(n) for n in self.nodes]
        for e in self.edges:
            dot.append(f'"{e["from"]}"->"{e["to"]}"{"" if self.constrains(e) else "[constraint=false]"};')
        band_ids = {k: [n['id'] for n in self.nodes if n['grp'] == k] for k in self.band_keys}
        w = self.cfg.get('funnel_weight', 0)
        for i in range(len(self.band_keys) - 1):
            z = f'__z{i}'
            dot.append(f'"{z}"[style=invis,width=0.01,height=0.01,label=""];')
            for nid in band_ids[self.band_keys[i]]:
                dot.append(f'"{nid}"->"{z}"[style=invis,weight={w}];')
            for nid in band_ids[self.band_keys[i + 1]]:
                dot.append(f'"{z}"->"{nid}"[style=invis,weight={w}];')
        dot.append('}')
        return dot

    # ---------- band decoration --------------------------------------------
    def band_cells(self, pos, X, Y):
        """Stage bands drawn behind everything: full-width tinted lanes named at
        the left margin, or a pill centred over each band. The lane tint is the
        fill only -- the renderer applies `opacity` to the shape and not to its
        text -- so an 8% lane keeps a legible label."""
        laid = [n for n in self.nodes if n['id'] in pos]
        if not laid:
            return []
        rects = {n['id']: node_rect(n, pos, X, Y) for n in laid}
        lane = self.cfg.get('band_label', 'lane') == 'lane'
        xlo = min(r[0] for r in rects.values()) - 40
        xhi = max(r[2] for r in rects.values()) + 40
        cells = []
        for b in self.bands:
            ns = [n for n in laid if n['grp'] == b['key']]
            if not ns:
                continue
            colour = PALETTE[b.get('palette', 'slate')][1]
            y0 = min(rects[n['id']][1] for n in ns)
            if lane:
                st = (f'rounded=0;html=1;whiteSpace=wrap;fillColor={colour};strokeColor=none;opacity=8;'
                      'verticalAlign=top;align=left;spacingLeft=12;spacingTop=4;'
                      f'fontSize=13;fontStyle=1;fontColor={colour};')
                y1 = max(rects[n['id']][3] for n in ns) + 16
                x, y, w, h = xlo, y0 - 34, xhi - xlo, (y1 - y0 + 34)
            else:
                st = (f'rounded=1;whiteSpace=wrap;html=1;fillColor={colour};strokeColor=none;'
                      'fontColor=#ffffff;fontSize=12;fontStyle=1;opacity=90;')
                w = max(150, 9 * len(b['label']))
                cx = (min(rects[n['id']][0] for n in ns) + max(rects[n['id']][2] for n in ns)) / 2
                x, y, h = cx - w / 2, y0 - 40, 24
            cells.append(f'<mxCell id="band_{b["key"]}" value="{esc(b["label"])}" style="{st}" '
                         f'vertex="1" parent="1"><mxGeometry x="{x:.0f}" y="{y:.0f}" '
                         f'width="{w:.0f}" height="{h:.0f}" as="geometry"/></mxCell>')
        return cells

    # ---------- build -------------------------------------------------------
    def build(self, out_path):
        self.validate()
        self.apply_links()
        self.size_nodes()
        pos, H, edgepts = run_dot(self.dot_source())
        if not pos:
            fail('Graphviz produced no layout; is `dot` installed? (run check_deps.py)')
        X, Y = transforms(H)
        bg = self.band_cells(pos, X, Y)
        mid = emit_edges(self.edges, edgepts, X, Y, '', {k: edge_style(v) for k, v in self.kinds.items()})
        fg = [emit_node(n, self.node_style(n), pos, X, Y) for n in self.nodes if n['id'] in pos]
        xml = wrap_mxfile(bg + mid + fg, self.cfg.get('title', 'diagram'),
                          self.cfg.get('id', 'diagram'),
                          self.cfg.get('page_width', 1600), self.cfg.get('page_height', 2400))
        open(out_path, 'w').write(xml)
        return len(fg), len(self.edges), len(xml)


def render(drawio_path, scale):
    """Write the SVG (and PNG when a rasteriser exists) next to the .drawio."""
    import drawio_to_png as r
    base = os.path.splitext(drawio_path)[0]
    verts, edges = r.collect(r.load_model(drawio_path))
    open(base + '.svg', 'w', encoding='utf-8').write(r.render_svg(verts, edges))
    print(f'{base}.svg: {len(verts)} nodes, {len(edges)} edges')
    tool = r.svg_to_png(base + '.svg', base + '.png', scale)
    print(f'{base}.png: rendered via {tool}' if tool else
          'PNG skipped: no rasteriser (pip install cairosvg, or install librsvg/inkscape/imagemagick)')


def main():
    ap = argparse.ArgumentParser(description='Build a .drawio codebase diagram from a graph JSON.')
    ap.add_argument('graph', help='graph JSON file')
    ap.add_argument('-o', '--output', help='output .drawio (default: alongside the JSON)')
    ap.add_argument('--render', action='store_true', help='also write .svg and .png')
    ap.add_argument('--check', action='store_true', help='validate the JSON and exit')
    ap.add_argument('--scale', type=float, default=2.0, help='PNG scale factor (default 2)')
    a = ap.parse_args()

    d = Diagram(a.graph)
    if a.check:
        n, e = d.validate()
        print(f'ok: {n} nodes, {e} edges')
        return
    out = a.output or os.path.splitext(a.graph)[0] + '.drawio'
    n, e, size = d.build(out)
    print(f'wrote {out}: nodes={n} edges={e} bytes={size}')
    if a.render:
        render(out, a.scale)


if __name__ == '__main__':
    main()
