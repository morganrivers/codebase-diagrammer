#!/usr/bin/env python3
"""
drawio_to_png.py — render a .drawio diagram to SVG (and PNG) locally, no
draw.io app required.

It reads the mxGraphModel out of a .drawio file (handling both the plain-XML
form this repo's generators produce *and* draw.io's compressed form:
base64-decode -> raw-inflate -> URL-decode), then draws the cells to SVG. PNG
is produced if a rasteriser is available (cairosvg, else rsvg-convert / inkscape
/ ImageMagick on PATH). SVG is always written.

It supports the shape/style subset these diagrams use (rounded rects, cylinders,
note shapes, pills, dotted container boxes; straight/curved edges with routed
waypoints, arrowheads, dashes, colours, labels). It is not a full draw.io
renderer — exotic styles from hand-edited files may not render perfectly.

Usage:
    python drawio_to_png.py diagram.drawio                 # -> diagram.svg (+ .png if possible)
    python drawio_to_png.py diagram.drawio -o out.png
    python drawio_to_png.py diagram.drawio --svg out.svg --scale 2
"""
import argparse, base64, html, math, os, re, shutil, subprocess, sys, urllib.parse, zlib
import defusedxml.ElementTree as ET


# ---------- read + decompress -------------------------------------------------
def load_model(path):
    root = ET.parse(path).getroot()
    gm = root.find('.//mxGraphModel')
    if gm is not None:
        return gm
    diag = root.find('.//diagram')
    if diag is None or not (diag.text and diag.text.strip()):
        sys.exit("error: no <mxGraphModel> or compressed <diagram> found.")
    raw = base64.b64decode(diag.text.strip())
    try:
        xml = zlib.decompress(raw, -15).decode('utf-8')   # raw DEFLATE
    except zlib.error:
        xml = zlib.decompress(raw).decode('utf-8')         # zlib-wrapped
    xml = urllib.parse.unquote(xml)
    return ET.fromstring(xml)


def parse_style(s):
    d = {}
    for part in (s or '').split(';'):
        if not part:
            continue
        if '=' in part:
            k, v = part.split('=', 1); d[k] = v
        else:
            d[part] = True
    return d


# ---------- collect cells -----------------------------------------------------
def collect(gm):
    root = gm.find('root')
    verts, edges = {}, []
    for el in root:
        link = ''
        tip = ''
        if el.tag == 'object':                      # label/tooltip wrapper
            cell = el.find('mxCell')
            if cell is None:
                continue
            nid = el.get('id'); label = el.get('label', '')
            link = el.get('link', '') or ''
            tip = el.get('tooltip', '') or ''
            style = cell.get('style', ''); geo = cell.find('mxGeometry')
        elif el.tag == 'mxCell':
            cell = el; nid = el.get('id'); label = el.get('value', '')
            style = el.get('style', ''); geo = el.find('mxGeometry')
        else:
            continue
        st = parse_style(style)
        if cell.get('edge') == '1':
            pts = []
            arr = geo.find('Array') if geo is not None else None
            if arr is not None:
                pts = [(float(p.get('x')), float(p.get('y'))) for p in arr.findall('mxPoint')]
            edges.append({'src': cell.get('source'), 'dst': cell.get('target'),
                          'pts': pts, 'style': st, 'label': label, 'link': link,
                          'tip': tip})
        elif cell.get('vertex') == '1' and geo is not None:
            verts[nid] = {'x': float(geo.get('x', 0)), 'y': float(geo.get('y', 0)),
                          'w': float(geo.get('width', 80)), 'h': float(geo.get('height', 40)),
                          'style': st, 'label': label, 'link': link, 'tip': tip}
    return verts, edges


# ---------- geometry helpers --------------------------------------------------
def center(v):
    return v['x'] + v['w'] / 2, v['y'] + v['h'] / 2


def border_point(v, tx, ty):
    cx, cy = center(v)
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    hw, hh = v['w'] / 2 + 2, v['h'] / 2 + 2
    s = min(hw / abs(dx) if dx else 1e9, hh / abs(dy) if dy else 1e9)
    return cx + dx * s, cy + dy * s


# ---------- SVG rendering -----------------------------------------------------
def esc(s):
    return html.escape(s, quote=True)


def label_lines(raw):
    if not raw:
        return []
    t = re.sub(r'<br\s*/?>', '\n', raw)
    t = re.sub(r'<[^>]+>', '', t)           # drop any other tags (e.g. <b>)
    return [html.unescape(x) for x in t.split('\n')]


def title_group(tip, svg):
    """Wrap rendered SVG in a <g> carrying the cell's tooltip as <title>, so the
    hover text the .drawio holds survives into the SVG a browser opens."""
    if not tip:
        return svg
    return f'<g><title>{esc(chr(10).join(label_lines(tip)))}</title>\n{svg}\n</g>'


def shape_svg(v):
    st = v['style']; x, y, w, h = v['x'], v['y'], v['w'], v['h']
    fill = st.get('fillColor', '#ffffff')
    if fill == 'none':
        fill = 'none'
    stroke = st.get('strokeColor', '#000000')
    stroke = 'none' if stroke == 'none' else stroke
    sw = st.get('strokeWidth', '1')
    opacity = float(st.get('opacity', 100)) / 100.0
    dash = ''
    if st.get('dashed') == '1':
        dash = f' stroke-dasharray="{st.get("dashPattern", "4 3").replace(" ", ",")}"'
    common = f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" opacity="{opacity}"{dash}'
    out = []
    if st.get('shape') == 'cylinder':
        e = 7
        out.append(f'<path d="M{x},{y+e} C{x},{y-2} {x+w},{y-2} {x+w},{y+e} '
                   f'L{x+w},{y+h-e} C{x+w},{y+h+2} {x},{y+h+2} {x},{y+h-e} Z" {common}/>')
        out.append(f'<path d="M{x},{y+e} C{x},{y+e+9} {x+w},{y+e+9} {x+w},{y+e}" '
                   f'fill="none" stroke="{stroke}" stroke-width="{sw}" opacity="{opacity}"/>')
    elif st.get('shape') == 'note':
        f = 14
        out.append(f'<path d="M{x},{y} L{x+w-f},{y} L{x+w},{y+f} L{x+w},{y+h} L{x},{y+h} Z" {common}/>')
        out.append(f'<path d="M{x+w-f},{y} L{x+w-f},{y+f} L{x+w},{y+f}" fill="none" '
                   f'stroke="{stroke}" stroke-width="{sw}" opacity="{opacity}"/>')
    else:                                   # rounded / plain rect (incl. pills, boxes)
        rx = h / 2 if st.get('arcSize') == '45' else (10 if st.get('rounded') == '1' else 0)
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" {common}/>')
    return '\n'.join(out)


def text_svg(x, y, w, h, lines, st):
    """Draw a cell's label, honouring align / verticalAlign / fontFamily the way
    draw.io does. Every cell obeys them, not only the borderless container
    boxes: a left-aligned stripe inside a filled card is the common case in the
    interfaces diagram, and centring it would put the text somewhere draw.io
    does not."""
    if not lines:
        return ''
    fc = st.get('fontColor', '#000000')
    fs = float(st.get('fontSize', 12))
    fam = st.get('fontFamily')
    fam_attr = f' font-family="{fam}"' if fam else ''
    bold = ' font-weight="700"' if st.get('fontStyle') in ('1', '3') else ''
    lh = fs * 1.25
    align = st.get('align', 'center')
    valign = st.get('verticalAlign', 'middle')
    if align == 'left':
        tx = x + float(st.get('spacingLeft', 12)); anchor = 'start'
    elif align == 'right':
        tx = x + w - float(st.get('spacingRight', 12)); anchor = 'end'
    else:
        tx = x + w / 2; anchor = 'middle'
    block = (len(lines) - 1) * lh
    if valign == 'top':
        y0 = y + float(st.get('spacingTop', 6)) + fs
    elif valign == 'bottom':
        y0 = y + h - float(st.get('spacingBottom', 6)) - block
    else:
        y0 = y + h / 2 - block / 2 + fs * 0.35
    return '\n'.join(
        f'<text x="{tx:.1f}" y="{y0+i*lh:.1f}" font-size="{fs}" fill="{fc}" '
        f'text-anchor="{anchor}"{fam_attr}{bold}>{esc(ln)}</text>'
        for i, ln in enumerate(lines))


def arrowhead(x1, y1, x2, y2, color):
    ang = math.atan2(y2 - y1, x2 - x1); L, W = 9, 4
    bx, by = x2 - L * math.cos(ang), y2 - L * math.sin(ang)
    p1 = (bx - W * math.sin(ang), by + W * math.cos(ang))
    p2 = (bx + W * math.sin(ang), by - W * math.cos(ang))
    return (f'<polygon points="{x2:.1f},{y2:.1f} {p1[0]:.1f},{p1[1]:.1f} '
            f'{p2[0]:.1f},{p2[1]:.1f}" fill="{color}"/>')


EDGE_FONT, EDGE_LINE = 9, 11   # edge label font size and line step


def edge_points(e, verts):
    """The drawn path of an edge, from source border through its waypoints to target border;
    None when an end is missing."""
    src, dst = verts.get(e['src']), verts.get(e['dst'])
    if not src or not dst:
        return None
    first = e['pts'][0] if e['pts'] else center(dst)
    last = e['pts'][-1] if e['pts'] else center(src)
    return [border_point(src, *first)] + e['pts'] + [border_point(dst, *last)]


def label_spots(e, pts):
    """(x, y, line) for each line of an edge label: centred on the middle segment of the path."""
    mx = (pts[len(pts)//2-1][0] + pts[len(pts)//2][0]) / 2
    my = (pts[len(pts)//2-1][1] + pts[len(pts)//2][1]) / 2
    return [(mx, my + i * EDGE_LINE, ln) for i, ln in enumerate(label_lines(e['label']))] if e['label'] else []


def edge_svg(e, verts):
    pts = edge_points(e, verts)
    if not pts:
        return ''
    st = e['style']
    color = st.get('strokeColor', '#9aa6b3')
    sw = st.get('strokeWidth', '1.4')
    dash = ' stroke-dasharray="5,4"' if st.get('dashed') == '1' else ''
    # smooth-ish path through points
    d = f'M{pts[0][0]:.1f},{pts[0][1]:.1f}'
    for px, py in pts[1:]:
        d += f' L{px:.1f},{py:.1f}'
    out = [f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"{dash}/>']
    ax, ay = pts[-2]; bx, by = pts[-1]
    out.append(arrowhead(ax, ay, bx, by, color))
    for x, y, ln in label_spots(e, pts):
        out.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{EDGE_FONT}" fill="#5b6675" '
                   f'text-anchor="middle" paint-order="stroke" stroke="#ffffff" '
                   f'stroke-width="3" stroke-linejoin="round">{esc(ln)}</text>')
    return '\n'.join(out)


def edge_extent(edges, verts):
    """Points the page must hold for the edges: every path point, and each label line's corners
    (width estimated at 0.6 em per character), so nothing Graphviz routes outside the boxes is cut."""
    out = []
    for e in edges:
        pts = edge_points(e, verts) or []
        out += pts
        for x, y, ln in label_spots(e, pts):
            half = len(ln) * EDGE_FONT * 0.3
            out += [(x - half, y - EDGE_FONT), (x + half, y + 3)]
    return out


def render_svg(verts, edges, pad=30):
    pts = edge_extent(edges, verts)
    xs = [v['x'] for v in verts.values()] + [v['x'] + v['w'] for v in verts.values()] + [p[0] for p in pts]
    ys = [v['y'] for v in verts.values()] + [v['y'] + v['h'] for v in verts.values()] + [p[1] for p in pts]
    minx, miny, maxx, maxy = min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad
    W, H = maxx - minx, maxy - miny
    body = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{W:.0f}" height="{H:.0f}" '
            f'viewBox="{minx:.0f} {miny:.0f} {W:.0f} {H:.0f}" '
            f'font-family="Helvetica,Arial,sans-serif">',
            f'<rect x="{minx:.0f}" y="{miny:.0f}" width="{W:.0f}" height="{H:.0f}" fill="#ffffff"/>']
    # order: container boxes/tags (behind) -> edges -> other nodes (front)
    boxes = [v for v in verts.values() if v['style'].get('fillColor') == 'none'
             or v['style'].get('strokeColor') == 'none']
    nodes = [v for v in verts.values() if v not in boxes]
    for v in boxes:
        body.append(shape_svg(v))
        body.append(text_svg(v['x'], v['y'], v['w'], v['h'], label_lines(v['label']), v['style']))
    # class/data attributes let delta_viewer.py find a node or edge by graph id
    for e in edges:
        link = e.get('link', '')
        body.append(f'<g class="edge" data-src="{esc(e["src"] or "")}" data-dst="{esc(e["dst"] or "")}">')
        if link:
            body.append(f'<a xlink:href="{esc(link)}" href="{esc(link)}" target="_blank">')
        body.append(title_group(e.get('tip', ''), edge_svg(e, verts)))
        if link:
            body.append('</a>')
        body.append('</g>')
    for nid, v in verts.items():
        if v not in nodes:
            continue
        link = v.get('link', '')
        body.append(f'<g class="node" data-id="{esc(nid)}">')
        if link:
            body.append(f'<a xlink:href="{esc(link)}" href="{esc(link)}" target="_blank">')
        body.append(title_group(v.get('tip', ''), shape_svg(v) + '\n' +
                                text_svg(v['x'], v['y'], v['w'], v['h'],
                                         label_lines(v['label']), v['style'])))
        if link:
            body.append('</a>')
        body.append('</g>')
    body.append('</svg>')
    return '\n'.join(body)


# ---------- rasterise ---------------------------------------------------------
def svg_to_png(svg_path, png_path, scale):
    """Rasterise with the first tool that works; its name, or None when none is installed.
    A tool that is installed but fails is reported, then the next one is tried."""
    try:
        import cairosvg
    except ImportError:
        cairosvg = None
    if cairosvg:
        try:
            cairosvg.svg2png(url=svg_path, write_to=png_path, scale=scale)
            return 'cairosvg'
        except Exception as e:
            print(f'warning: cairosvg failed on {svg_path}: {e}', file=sys.stderr)
    for tool, cmd in (('rsvg-convert', ['rsvg-convert', '-z', str(scale), '-o', png_path, svg_path]),
                      ('inkscape', ['inkscape', svg_path, '--export-type=png', f'--export-filename={png_path}']),
                      ('convert', ['convert', '-density', str(int(96 * scale)), svg_path, png_path])):
        if not shutil.which(tool):
            continue
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return tool
        print(f'warning: {tool} failed on {svg_path}: {r.stderr.strip()[:300]}', file=sys.stderr)
    return None


def render(drawio_path, svg_path=None, png_path=None, scale=2.0):
    """Write the SVG, and the PNG when a rasteriser works, next to the .drawio by default."""
    base = os.path.splitext(drawio_path)[0]
    svg_path, png_path = svg_path or base + '.svg', png_path or base + '.png'
    verts, edges = collect(load_model(drawio_path))
    open(svg_path, 'w', encoding='utf-8').write(render_svg(verts, edges))
    print(f'{svg_path}: {len(verts)} nodes, {len(edges)} edges')
    tool = svg_to_png(svg_path, png_path, scale)
    if tool:
        print(f'{png_path}: rendered via {tool}')
    else:
        print(f'warning: PNG skipped, no working rasteriser (pip install cairosvg, or install '
              f'librsvg / inkscape / imagemagick); {svg_path} can be opened in a browser', file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Render a .drawio to SVG/PNG locally (no draw.io app).")
    ap.add_argument('input', help='.drawio file')
    ap.add_argument('-o', '--output', help='PNG output path (default: <input>.png)')
    ap.add_argument('--svg', help='SVG output path (default: <input>.svg)')
    ap.add_argument('--scale', type=float, default=2.0, help='PNG scale factor (default 2)')
    a = ap.parse_args()
    render(a.input, a.svg, a.output, a.scale)


if __name__ == '__main__':
    main()
