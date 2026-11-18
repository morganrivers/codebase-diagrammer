#!/usr/bin/env python3
"""
check_edges.py: compare a diagram's arrows with the imports in the code.

The import graph of a whole repo is too big to draw, so this only uses it to
check the diagram. Files with a box are stops; files without a box are passed
through, so `A imports helper.ts imports B` counts as A to B. Two lists come
out:

  in the code, not drawn   box A reaches box B through imports, and no arrow
                           or ✳ chip joins them
  drawn, no import         an arrow between two code boxes with no import path
                           either way: a wrong arrow, or a link made another
                           way (HTTP call, table, queue, job name)

A ✳ chip counts as its shared node (`proxy_of`). Arrows are compared without
direction, because an arrow follows the data and an import may run the other
way. What imports cannot show, and this does not check: tables (`data` nodes),
external services (`ext`), HTTP calls between routes, queues and jobs.

Reads TypeScript/JavaScript (relative imports, tsconfig.json `paths`) and
Python (absolute and relative imports, from the repo root or `src/`).

Usage:
    python check_edges.py docs/diagram/<name>.json [--ref origin/main]

--ref reads the code at that git ref instead of the working tree, for a
diagram that describes a branch other than the one checked out.
"""
import argparse, json, os, posixpath, re, sys
from collections import deque
from build_diagram import git, fail, RepoLinks, effective_file, covers

TS_EXT = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs')
# `import type` / `export type` statements: compile time only, not a connection.
TS_TYPE_ONLY = re.compile(r'''^\s*(?:import|export)\s+type\b[^;]*?from\s*['"][^'"]+['"]''', re.M)
TS_NAMED = re.compile(r'''\b(?:import|export)\s+(?!type\b)([^;'"]*?)\s+from\s*['"]([^'"]+)['"]''')
TS_IMPORT = re.compile(r'''(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)['"]([^'"]+)['"]''', re.M)
# `from x import (a,\n b)` may span lines; `from x import a, b` ends at the line end.
PY_IMPORT = re.compile(r'^[ \t]*(?:from[ \t]+(\.*[\w.]*)[ \t]+import[ \t]+(\([^)]*\)|[\w*, \t]+)|import[ \t]+([\w.]+))', re.M)
UNCHECKED_TYPES = {'data', 'ext', 'note'}


class Source:
    """Repo files and their text, from the working tree or from a git ref."""

    def __init__(self, root, ref=None):
        self.root, self.ref = root, ref
        listing = git(root, 'ls-tree', '-r', '--name-only', ref) if ref else git(root, 'ls-files')
        if not listing:
            fail(f'no files listed in {root}' + (f' at {ref}' if ref else ''))
        self.files = set(listing.splitlines())

    def read(self, path):
        if self.ref:
            return git(self.root, 'show', f'{self.ref}:{path}')
        try:
            return open(os.path.join(self.root, path), encoding='utf-8', errors='replace').read()
        except OSError:
            return ''


class ImportGraph:
    """file -> files it imports, resolved inside the repo; `names` keeps what each import names."""

    def __init__(self, src):
        self.src = src
        self.aliases = self._ts_aliases()
        self.names = {}   # (file, imported file) -> the imported names, as written
        self.type_edges = {}   # file -> files it imports types from only: used, but not a connection
        self.edges = {f: self._imports(f) for f in src.files if f.endswith(TS_EXT + ('.py',))}

    def _ts_aliases(self):
        """tsconfig.json `paths` as (prefix, target dir) pairs, e.g. ('@/', '')."""
        text = self.src.read('tsconfig.json') if 'tsconfig.json' in self.src.files else ''
        # JSONC: drop comments outside strings ("@/*" holds a /*), then trailing commas.
        text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', lambda m: m.group(1) or '', text, flags=re.S)
        text = re.sub(r'("(?:\\.|[^"\\])*")|,(\s*[}\]])', lambda m: m.group(1) or m.group(2), text)
        try:
            opts = json.loads(text).get('compilerOptions', {}) if text else {}
        except json.JSONDecodeError as e:
            print(f'warning: tsconfig.json not parsed ({e}); @/ style aliases are not resolved', file=sys.stderr)
            return []
        base = opts.get('baseUrl', '.')
        out = []
        for key, targets in opts.get('paths', {}).items():
            if key.endswith('/*') and targets and targets[0].endswith('/*'):
                out.append((key[:-1], posixpath.normpath(posixpath.join(base, targets[0][:-1])).lstrip('./')))
        return out

    def _ts_file(self, stem):
        """The file an extensionless or .js import names."""
        stem = posixpath.normpath(stem)
        cands = [stem] if stem.endswith(TS_EXT) and stem in self.src.files else []
        bare = re.sub(r'\.(m|c)?jsx?$', '', stem)
        cands += [bare + e for e in TS_EXT] + [f'{bare}/index{e}' for e in TS_EXT]
        return next((c for c in cands if c in self.src.files), None)

    def _imports(self, path):
        text = self.src.read(path)
        if path.endswith('.py'):
            return self._py_imports(path, text)
        out = set()
        code = TS_TYPE_ONLY.sub('', text)
        types = {self._ts_resolve(path, s) for m in TS_TYPE_ONLY.finditer(text) for s in TS_IMPORT.findall(m.group(0))}
        self.type_edges[path] = types - {None}
        named = {}
        for what, spec in TS_NAMED.findall(code):
            named[spec] = ', '.join(filter(None, [named.get(spec), ' '.join(what.split())]))
        for spec in TS_IMPORT.findall(code):
            target = self._ts_resolve(path, spec)
            if target:
                out.add(target)
                self.names[(path, target)] = named.get(spec, '')
        return out

    def _ts_resolve(self, path, spec):
        """The repo file an import specifier in `path` names, or None (a package)."""
        if spec.startswith('.'):
            return self._ts_file(posixpath.join(posixpath.dirname(path), spec))
        return next((self._ts_file(tgt + spec[len(pre):]) for pre, tgt in self.aliases
                     if spec.startswith(pre)), None)

    def _py_module(self, dotted):
        rel = dotted.replace('.', '/')
        for prefix in ('', 'src/'):
            for cand in (f'{prefix}{rel}.py', f'{prefix}{rel}/__init__.py'):
                if cand in self.src.files:
                    return cand
        return None

    def _py_imports(self, path, text):
        out = set()
        pkg = posixpath.dirname(path).replace('/', '.')
        for frm, names, plain in PY_IMPORT.findall(text):
            if plain:
                mods = [plain]
            else:
                dots = len(frm) - len(frm.lstrip('.'))
                base = frm.lstrip('.')
                if dots:
                    parts = pkg.split('.') if pkg else []
                    parts = parts[:len(parts) - (dots - 1)] if dots > 1 else parts
                    base = '.'.join(p for p in parts + ([base] if base else []) if p)
                names = [n.strip() for n in names.replace('(', ' ').replace(')', ' ').split(',') if n.strip()]
                # `from pkg import mod` imports a module when one exists by that name.
                mods = [base] + [f'{base}.{n}' for n in names if n != '*']
            for m in mods:
                target = self._py_module(m)
                if target and target != path:
                    out.add(target)
                    self.names[(path, target)] = plain or ', '.join(names)
        return out


class DiagramCheck:
    def __init__(self, json_path, ref=None):
        graph = json.load(open(json_path))
        self.nodes = {n['id']: n for n in graph['nodes']}
        links = RepoLinks(graph.get('config', {}), os.path.dirname(os.path.abspath(json_path)))
        self.src = Source(links.root, ref)
        self.imports = ImportGraph(self.src)
        self.home = {nid: n.get('proxy_of', nid) for nid, n in self.nodes.items()}
        self.boxes = {nid: effective_file(n, self.nodes).rstrip('/') for nid, n in self.nodes.items()
                      if nid == self.home[nid] and n.get('type', 'script') not in UNCHECKED_TYPES
                      and effective_file(n, self.nodes)}
        self.box_of = self._box_of_files()
        self.drawn = {frozenset((self.home[e['from']], self.home[e['to']])) for e in graph['edges']}
        self.edges = graph['edges']

    def _box_of_files(self):
        """Each repo file -> the box whose file is it, or the deepest box directory holding it."""
        out = {}
        for f in self.src.files:
            hits = [(len(p), nid) for nid, p in self.boxes.items() if covers(p, f)]
            if hits:
                out[f] = max(hits)[1]
        return out

    def reach(self, box):
        """Boxes this box imports, through files with no box; with one example path each."""
        starts = [f for f, b in self.box_of.items() if b == box]
        seen = {f: [f] for f in starts}
        found = {}
        queue = deque(starts)
        while queue:
            f = queue.popleft()
            for g in self.imports.edges.get(f, ()):
                if g in seen:
                    continue
                seen[g] = seen[f] + [g]
                other = self.box_of.get(g)
                if other and other != box:
                    found.setdefault(other, seen[g])
                elif not other:
                    queue.append(g)
        return found

    def run(self):
        code = {}
        for box in self.boxes:
            for other, path in self.reach(box).items():
                code.setdefault(frozenset((box, other)), (box, other, path))
        missing = [v for k, v in code.items() if k not in self.drawn]
        unbacked, skipped = [], 0
        for e in self.edges:
            a, b = self.home[e['from']], self.home[e['to']]
            if a == b or a not in self.boxes or b not in self.boxes:
                skipped += 1
                continue
            if frozenset((a, b)) not in code:
                unbacked.append(e)
        return missing, unbacked, skipped


def label(node):
    return node['lbl'].split('\n')[0]


def main():
    ap = argparse.ArgumentParser(description="Compare a diagram's arrows with the imports in the code.")
    ap.add_argument('graph', help='graph JSON')
    ap.add_argument('--ref', help='read the code at this git ref instead of the working tree')
    a = ap.parse_args()
    check = DiagramCheck(a.graph, a.ref)
    missing, unbacked, skipped = check.run()
    n = check.nodes
    print(f'{a.graph}: {len(check.boxes)} code boxes, {len(check.imports.edges)} source files read'
          + (f' at {a.ref}' if a.ref else ''))
    direct = [m for m in missing if len(m[2]) == 2]
    chained = sorted((m for m in missing if len(m[2]) > 2), key=lambda m: len(m[2]))
    print(f'\nIn the code, not drawn: direct imports ({len(direct)}):')
    for box, other, path in sorted(direct, key=lambda m: (label(n[m[0]]), label(n[m[1]]))):
        what = check.imports.names.get(tuple(path), '')
        print(f'  {label(n[box])} -> {label(n[other])}\n      {" -> ".join(path)}'
              + (f'\n      imports: {what[:160]}' if what else ''))
    print(f'\nIn the code, not drawn: through files with no box ({len(chained)}), shortest first;'
          ' long chains are often incidental (a header, a shared helper):')
    for box, other, path in chained:
        print(f'  {label(n[box])} -> {label(n[other])}  (via {len(path) - 2} files)\n      {" -> ".join(path)}')
    print(f'\nDrawn, no import path either way ({len(unbacked)}): check each is an HTTP call, table, queue or job')
    for e in unbacked:
        print(f'  {label(n[e["from"]])} -> {label(n[e["to"]])}  [{e.get("label", "").replace(chr(10), " ")}]')
    print(f'\nNot checked: {skipped} arrows touching a table, external service, note, a box with no file, or the same box.')


if __name__ == '__main__':
    main()
