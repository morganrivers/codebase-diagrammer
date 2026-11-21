#!/usr/bin/env python3
"""
status_page.py: one self-contained HTML page showing where a piece of work stands, next to its
codebase diagram and delta page.

The input is a JSON file kept in the repo (for example docs/diagram/<name>_status.json):

    {
      "title": "Octen switch",
      "updated": "2026-11-13 19:40",
      "summary": "One paragraph on where things stand.",
      "links": [{"label": "Commits on the diagram", "href": "delta.html"}],
      "workstreams": [
        {"name": "Session A", "about": "what this stream does",
         "items": [{"text": "...", "state": "done|doing|planned|blocked", "note": "optional detail"}]}
      ],
      "tables": [
        {"title": "...", "caption": "...", "columns": ["setup", "@10", "@100"],
         "rows": [["Octen alone", 53, 121]], "best_rows": [0], "notes": ["..."]}
      ],
      "notes": ["free text shown at the bottom"]
    }

Every string is HTML-escaped; nothing in the JSON can inject markup.

Usage:
    python status_page.py docs/diagram/<name>_status.json [-o docs/diagram/<name>_status.html]
"""
import argparse, html, json, os, sys

STATES = {  # state -> (mark, label)
    'done': ('✓', 'done'),
    'doing': ('◐', 'in progress'),
    'planned': ('○', 'planned'),
    'blocked': ('✕', 'blocked'),
}

CSS = """
:root { --bg:#fbfaf7; --fg:#1d1d1b; --muted:#6b6a64; --card:#ffffff; --line:#e4e1d8;
  --done:#2f7d4f; --doing:#b7791f; --planned:#6b6a64; --blocked:#b23a3a; --best:#eef6ee; --link:#2457a6; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#17181a; --fg:#e8e6e1; --muted:#a3a19a; --card:#202225; --line:#34363a;
  --done:#6fcf97; --doing:#f2c14e; --planned:#a3a19a; --blocked:#f28b82; --best:#1f3326; --link:#8ab4f8; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }
main { max-width:1100px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:24px; margin:0 0 4px; } h2 { font-size:18px; margin:28px 0 10px; }
.meta { color:var(--muted); font-size:13px; }
.links a { color:var(--link); margin-right:16px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }
.card h3 { margin:0 0 2px; font-size:16px; } .about { color:var(--muted); font-size:13px; margin-bottom:8px; }
ul.items { list-style:none; padding:0; margin:0; }
ul.items li { display:grid; grid-template-columns:22px 1fr; gap:4px; padding:4px 0; border-top:1px solid var(--line); }
ul.items li:first-child { border-top:none; }
.mark { font-weight:700; text-align:center; }
.done .mark { color:var(--done); } .doing .mark { color:var(--doing); }
.planned .mark { color:var(--planned); } .blocked .mark { color:var(--blocked); }
.note { color:var(--muted); font-size:13px; }
.count { color:var(--muted); font-size:13px; margin-left:6px; }
.tablewrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; background:var(--card); font-size:14px; }
th, td { border:1px solid var(--line); padding:5px 8px; text-align:right; white-space:nowrap; }
th:first-child, td:first-child { text-align:left; white-space:normal; }
tr.best td { background:var(--best); font-weight:600; }
.caption { color:var(--muted); font-size:13px; margin:4px 0 8px; }
"""


def e(value):
    return html.escape(str(value), quote=True)


def render_workstream(ws):
    items = ws.get('items', [])
    counts = {s: sum(1 for i in items if i.get('state') == s) for s in STATES}
    parts = [f'<div class="card"><h3>{e(ws["name"])}'
             f'<span class="count">{counts["done"]} of {len(items)} done</span></h3>']
    if ws.get('about'):
        parts.append(f'<div class="about">{e(ws["about"])}</div>')
    parts.append('<ul class="items">')
    for item in items:
        state = item.get('state')
        if state not in STATES:
            sys.exit(f'unknown state {state!r} in item {item.get("text")!r}; use one of {sorted(STATES)}')
        mark, label = STATES[state]
        note = f'<div class="note">{e(item["note"])}</div>' if item.get('note') else ''
        parts.append(f'<li class="{state}"><span class="mark" title="{label}">{mark}</span>'
                     f'<div>{e(item["text"])}{note}</div></li>')
    parts.append('</ul></div>')
    return ''.join(parts)


def render_table(t):
    cols = t['columns']
    best = set(t.get('best_rows', []))
    out = [f'<h2>{e(t["title"])}</h2>']
    if t.get('caption'):
        out.append(f'<div class="caption">{e(t["caption"])}</div>')
    out.append('<div class="tablewrap"><table><thead><tr>' + ''.join(f'<th>{e(c)}</th>' for c in cols)
               + '</tr></thead><tbody>')
    for i, row in enumerate(t['rows']):
        if len(row) != len(cols):
            sys.exit(f'table {t["title"]!r}: row {i} has {len(row)} cells for {len(cols)} columns')
        cls = ' class="best"' if i in best else ''
        out.append(f'<tr{cls}>' + ''.join(f'<td>{e(c)}</td>' for c in row) + '</tr>')
    out.append('</tbody></table></div>')
    for note in t.get('notes', []):
        out.append(f'<div class="caption">{e(note)}</div>')
    return ''.join(out)


def render(status):
    links = ''.join(f'<a href="{e(l["href"])}">{e(l["label"])}</a>' for l in status.get('links', []))
    body = [f'<h1>{e(status["title"])}</h1>',
            f'<div class="meta">Updated {e(status.get("updated", ""))}</div>']
    if links:
        body.append(f'<p class="links">{links}</p>')
    if status.get('summary'):
        body.append(f'<p>{e(status["summary"])}</p>')
    if status.get('workstreams'):
        body.append('<h2>Work</h2><div class="grid">'
                    + ''.join(render_workstream(w) for w in status['workstreams']) + '</div>')
    body += [render_table(t) for t in status.get('tables', [])]
    if status.get('notes'):
        body.append('<h2>Notes</h2><ul>' + ''.join(f'<li>{e(n)}</li>' for n in status['notes']) + '</ul>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{e(status["title"])}</title><style>{CSS}</style></head>'
            f'<body><main>{"".join(body)}</main></body></html>\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('status_json')
    ap.add_argument('-o', '--out')
    a = ap.parse_args()
    status = json.load(open(a.status_json))
    for key in ('title',):
        if key not in status:
            sys.exit(f'{a.status_json}: missing required key {key!r}')
    out = a.out or os.path.splitext(a.status_json)[0] + '.html'
    with open(out, 'w') as f:
        f.write(render(status))
    print(out)


if __name__ == '__main__':
    main()
