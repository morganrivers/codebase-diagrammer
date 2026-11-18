#!/usr/bin/env python3
"""
delta_viewer.py: one self-contained HTML page that shows what the latest
commits on a branch changed, drawn on the repo's codebase diagrams.

Pick a commit on the left: boxes whose `file` the commit touched are outlined
(green added, amber modified, red deleted) with their +/- line counts, the rest
fade. A merge commit (a release or a PR) lists the commits it brought in; pick
one of those to see that step alone. Changed files that no box covers are
listed too, so nothing the commit did is hidden.

Unmerged branches (draft PRs) can be listed above the commits with --branch or
--draft-prs: each shows its whole diff against its merge base with its PR's
base branch (for a stacked PR, the branch below it; with no PR, --against),
and its commits inside.

The "PR view" toggle in the header shows one card per pull request: a PR's own
commits are folded into it, so its card, diff, file notes and summary cover
the whole PR (merge commit against its first parent, or the branch against its
base). A release that merged other PRs still lists those PRs inside it. Merged
PRs are matched to their merge (or squash) commit with one gh call; their title
and body go on the card and into the summary.

Hovering or focusing a card in the list (branch, commit, or a commit inside
one) shows a 2 to 4 sentence summary of what it does and why, written by Claude
at build time from the messages, the branch's PR body (gh), the per file notes
and the diff, and cached by commit hash (change_notes.py, CommitSummaries).

The "Code metrics" tab plots, for each listed entry (not the commits inside
a merge), the code's cyclomatic complexity, file count, lines of code, average
and longest file length, and the 90th percentile file length (code_metrics.py).

Right-click a box or a file row to open the local file: the page follows a
u-open:<url-encoded absolute path> link, which needs a handler for that
scheme on the machine (Morgan's laptop: ~/.local/bin/u-open-url, opens with u).

Usage:
    python delta_viewer.py docs/diagram/a.json [docs/diagram/b.json ...] \
        [--ref origin/main] [-n 5] [--branch REF ...] [--against origin/staging] \
        [--pr N ...] [--draft-prs] [-o docs/diagram/delta.html]

Every graph JSON must already be built (its .drawio sits next to it) and all
must describe the same repo. The script only reads git; run `git fetch` first
if --ref is a remote branch.
"""
import argparse, json, os, re, sys
from build_diagram import git, fail, RepoLinks, effective_file
from drawio_to_png import load_model, collect, render_svg
from code_metrics import CodeMetrics, CODE_EXTS
from change_notes import ChangeNotes, CommitSummaries

EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
MAX_CHILDREN = 60         # commits listed under one merge


class DiagramView:
    """One built diagram: its SVG and a path -> box lookup."""

    def __init__(self, json_path):
        self.json_path = json_path
        graph = json.load(open(json_path))
        drawio = os.path.splitext(json_path)[0] + '.drawio'
        if not os.path.exists(drawio):
            fail(f'{drawio} missing: build {json_path} first')
        if os.path.getmtime(drawio) < os.path.getmtime(json_path):
            print(f'warning: {drawio} is older than {json_path}; the page shows the old layout',
                  file=sys.stderr)
        verts, edges = collect(load_model(drawio))
        self.svg = render_svg(verts, edges)
        self.name = graph.get('config', {}).get('title') or os.path.basename(os.path.splitext(json_path)[0])
        self.links = RepoLinks(graph.get('config', {}), os.path.dirname(os.path.abspath(json_path)))
        by_id = {n['id']: n for n in graph['nodes']}
        # A ✳ chip is a copy of its shared node: it lights up and lists files with it.
        files = {n['id']: effective_file(n, by_id) for n in graph['nodes']}
        self.nodes = {n['id']: {'lbl': n['lbl'], 'file': files[n['id']], 'proxy_of': n.get('proxy_of'),
                               'desc': n.get('desc', '')}
                      for n in graph['nodes']}
        self.paths = [(f.rstrip('/'), nid) for nid, f in files.items() if f]

    def boxes_for(self, path):
        """Ids of boxes whose file is this path, or a directory containing it."""
        return [nid for f, nid in self.paths if path == f or path.startswith(f + '/')]


class Commit:
    """One commit's change set against its first parent."""

    def __init__(self, root, sha, base=None):
        fmt = '%H%x00%P%x00%an%x00%ad%x00%s%x00%b'
        out = git(root, 'show', '-s', f'--format={fmt}', '--date=short', sha)
        if not out:
            fail(f'commit {sha!r} not found in {root}')
        full, parents, self.author, self.date, self.subject, self.body = out.split('\x00', 5)
        self.sha = full
        self.key = full
        self.url = ''
        self.pr = None         # gh fields of the PR this commit merged, or None (attach_prs)
        self.pr_error = ''
        self.parents = parents.split()
        self.base = base or (self.parents[0] if self.parents else EMPTY_TREE)
        self.files = self._files(root)
        self.patch_note = self._patches(root)

    def _files(self, root):
        status = {}
        for line in git(root, 'diff', '--no-renames', '--name-status', self.base, self.sha).splitlines():
            code, path = line.split('\t', 1)
            status[path] = code[0]
        files = []
        for rec in git(root, 'diff', '--no-renames', '--numstat', '-z', self.base, self.sha).split('\0'):
            if not rec:
                continue
            add, dele, path = rec.split('\t', 2)
            files.append({'path': path, 'status': status.get(path, 'M'),
                          'add': int(add) if add != '-' else 0,
                          'del': int(dele) if dele != '-' else 0,
                          'binary': add == '-', 'patch': ''})
        assert len(files) == len(status), f'{self.sha[:7]}: numstat and name-status disagree'
        return files

    def _patches(self, root):
        """Attach each file's full patch text. Nothing is cut: the panel scrolls."""
        text = git(root, 'diff', '--no-renames', self.base, self.sha)
        by_path = {f['path']: f for f in self.files}
        for chunk in text.split('\ndiff --git '):
            head = chunk.split('\n', 1)[0].removeprefix('diff --git ')
            f = by_path.get(head.split(' b/', 1)[-1] if ' b/' in head else '')
            if f:
                f['patch'] = chunk
        missing = [f['path'] for f in self.files if not f['patch'] and not f['binary']]
        return f'No patch text found for: {", ".join(missing)}' if missing else ''

    def inner_range(self):
        """Git range of the commits a merge brought in, along the merged branch's
        first parents; None for a plain commit."""
        return f'{self.parents[0]}..{self.parents[1]}' if len(self.parents) > 1 else None

    def children(self, root):
        rng = self.inner_range()
        if not rng:
            return [], ''
        shas = git(root, 'log', '--first-parent', '--format=%H', rng).split()
        note = f'{len(shas)} commits inside; only the newest {MAX_CHILDREN} listed.' \
            if len(shas) > MAX_CHILDREN else ''
        return [Commit(root, s) for s in shas[:MAX_CHILDREN]], note

    def inner_messages(self, root):
        """Messages of the commits inside (inner_range), newest first; they say why its files changed."""
        rng = self.inner_range()
        if not rng:
            return []
        out = git(root, 'log', '--first-parent', '--format=%s%n%b%x00', rng)
        return [m.strip() for m in out.split('\0') if m.strip()]

    def as_dict(self, views, base_url, notes=None, summaries=None):
        by_path = notes.for_commit(self) if notes else {}
        for f in self.files:
            f['boxes'] = {v.name: v.boxes_for(f['path']) for v in views}
            f['note'] = by_path.get(f['path'], '')
        branch = isinstance(self, BranchDelta)
        # A branch's link shows its whole diff (GitHub compare), not only its newest commit.
        link = f'{base_url}/compare/{self.base}...{self.sha}' if branch else f'{base_url}/commit/{self.sha}'
        return {'sha': self.key, 'short': self.sha[:7], 'author': self.author, 'date': self.date,
                'subject': self.subject, 'body': self.body.strip(), 'merge': len(self.parents) > 1,
                'branch': branch, 'url': self.url or (link if base_url else ''),
                'files': self.files, 'patch_note': self.patch_note,
                'notes_error': notes.error_for(self) if notes else 'Change notes were skipped for this build (--no-notes).',
                'summary': summaries.for_commit(self) if summaries else '',
                'summary_error': summaries.error_for(self) if summaries else 'Summaries were skipped for this build (--no-notes).',
                'pr': self.pr, 'pr_error': self.pr_error, 'against_note': getattr(self, 'against_note', '')}


class BranchDelta(Commit):
    """A branch not merged yet, such as a draft PR: its whole diff against its
    merge base with `against`, listed with the commits it holds."""

    def __init__(self, root, ref, against, title='', url='', pr=None, against_note=''):
        tip = git(root, 'rev-parse', '--verify', '-q', f'{ref}^{{commit}}').strip()
        if not tip:
            fail(f'branch {ref!r} not found in {root}; run git fetch first')
        base = git(root, 'merge-base', against, tip).strip()
        if not base:
            fail(f'{ref!r} has no merge base with {against!r}')
        super().__init__(root, tip, base=base)
        self.key = f'branch:{ref}'
        self.subject = title or f'{ref} (not merged)'
        why = f'the base branch of PR #{pr["number"]}' if pr else against_note
        self.body = f'Branch {ref}, diff against its merge base with {against} ({base[:7]}): {why}.'
        self.url = url
        self.pr = pr                    # gh fields of its PR (PR_FIELDS), or None
        self.against_note = against_note  # set when the branch is not diffed against its PR base

    def inner_range(self):
        return f'{self.base}..{self.sha}'


PR_FIELDS = 'number,title,headRefName,baseRefName,url,isDraft,body,state'
GH_TRIES = 3   # the GitHub API sometimes drops a call (unexpected EOF)


def gh_json(root, *args):
    """gh's JSON output. Raises RuntimeError after GH_TRIES failed tries."""
    import subprocess
    for _ in range(GH_TRIES):
        r = subprocess.run(['gh', *args], cwd=root, capture_output=True, text=True)
        if r.returncode == 0:
            return json.loads(r.stdout)
    raise RuntimeError(f'gh {" ".join(args)} failed {GH_TRIES} times: {r.stderr.strip()[:200]}')


MERGED_PR_LIMIT = 1000
MERGE_SUBJECT = re.compile(r'^Merge pull request #(\d+) ')


def merged_prs(root):
    """{merge commit sha: gh fields} for merged PRs (merge commit or squash commit alike),
    and '' or why it is incomplete. gh failing is reported, not fatal: the page then shows
    merged PRs without their PR text and says why."""
    try:
        prs = gh_json(root, 'pr', 'list', '--state', 'merged', '--limit', str(MERGED_PR_LIMIT),
                      '--json', PR_FIELDS + ',mergeCommit')
    except RuntimeError as e:
        print(f'warning: merged PRs not read: {e}', file=sys.stderr)
        return {}, f'merged PRs not read from GitHub ({e})'
    note = f'only the newest {MERGED_PR_LIMIT} merged PRs read' if len(prs) >= MERGED_PR_LIMIT else ''
    return {p['mergeCommit']['oid']: p for p in prs if p.get('mergeCommit')}, note


def attach_prs(commits, by_merge, why):
    """Give each merged PR's commit its PR (title, body, link). A merge commit whose subject
    names a PR that gh did not return is warned about and marked on the page."""
    for c in commits:
        if isinstance(c, BranchDelta):
            continue
        c.pr = by_merge.get(c.sha)
        m = MERGE_SUBJECT.match(c.subject)
        if m and not c.pr:
            c.pr_error = f'PR #{m.group(1)} text not read: {why or "gh did not return it as merged"}'
            print(f'warning: {c.sha[:7]}: {c.pr_error}', file=sys.stderr)


def gh_or_fail(root, *args):
    try:
        return gh_json(root, *args)
    except RuntimeError as e:
        fail(str(e))


def pr_for_head(root, head):
    """The open PR from branch `head`, else its newest PR in any state; None if it has none."""
    prs = gh_json(root, 'pr', 'list', '--head', head, '--state', 'all', '--json', PR_FIELDS)
    return ([p for p in prs if p['state'] == 'OPEN'] or prs or [None])[0]


def branch_delta(root, ref, default_against, pr=None, title='', url=''):
    """The branch diffed against its PR's base branch (for a stacked PR, the branch below it).
    With no PR, or when gh fails, against default_against; the card says so."""
    remotes = set(git(root, 'remote').split())
    first, _, rest = ref.partition('/')
    remote, head = (first, rest) if first in remotes and rest else ('origin', ref)
    note = ''
    if pr is None:
        try:
            pr = pr_for_head(root, head)
            note = '' if pr else f'no PR found for {head}'
        except RuntimeError as e:
            note = f'PR not read ({e})'
    against = f'{remote}/{pr["baseRefName"]}' if pr else default_against
    if pr and not git(root, 'rev-parse', '--verify', '-q', f'{against}^{{commit}}').strip():
        note = f'base branch {against} of PR #{pr["number"]} not found (run git fetch)'
        against = default_against
    if note:
        note = f'{note}, so diffed against {default_against}'
        print(f'warning: {ref}: {note}', file=sys.stderr)
    return BranchDelta(root, ref, against, title=title, url=url, pr=pr, against_note=note)


def pr_delta(root, p, default_against):
    """A PR listed by number or as a draft: titled with the PR, linked to it."""
    kind = 'Draft PR' if p['isDraft'] else 'PR'
    return branch_delta(root, f'origin/{p["headRefName"]}', default_against, pr=p,
                        title=f'{kind} #{p["number"]}: {p["title"]}', url=p['url'])


def as_entry(c, kids, note, views, base_url, notes, summaries):
    d = c.as_dict(views, base_url, notes, summaries)
    d['children'] = [k.as_dict(views, base_url, notes, summaries) for k in kids]
    d['children_note'] = note
    return d


def first_parent_commits(root, ref, n):
    shas = git(root, 'log', '--first-parent', f'-{n}', '--format=%H', ref).split()
    if not shas:
        fail(f'no commits found for {ref!r} in {root}')
    return [Commit(root, sha) for sha in shas]


def collect_metrics(root, tops, exts):
    """Code metrics at each listed entry (not the commits inside merges), oldest first."""
    cm = CodeMetrics(root, exts)
    points = []
    # Commit time, not author time: a rebased branch keeps its old author dates.
    when = {c.key: int(git(root, 'show', '-s', '--format=%ct', c.sha)) for c in tops}
    for c in sorted(tops, key=lambda c: when[c.key]):
        pr = re.search(r'#(\d+)', c.subject)
        label = f'#{pr.group(1)}' if pr else c.key.rsplit('/', 1)[-1] if isinstance(c, BranchDelta) else c.sha[:7]
        points.append({'key': c.key, 'label': label, 'subject': c.subject, 'date': git(root, 'show', '-s', '--format=%cs', c.sha),
                       'branch': isinstance(c, BranchDelta), **cm.at(c.sha)})
    cm.close()
    return {'exts': list(exts), 'points': points}


def main():
    ap = argparse.ArgumentParser(description='HTML viewer: latest commits drawn on codebase diagrams.')
    ap.add_argument('graphs', nargs='+', help='built graph JSON files, same repo')
    ap.add_argument('--ref', default='origin/main', help='branch or ref to read (default origin/main)')
    ap.add_argument('-n', type=int, default=5, help='number of first-parent commits (default 5)')
    ap.add_argument('--branch', action='append', default=[], metavar='REF',
                    help='also show this unmerged branch (e.g. origin/morgan/eva-312) as one diff '
                         'against its merge base with --against; repeatable')
    ap.add_argument('--against', default='origin/staging',
                    help='what a branch is diffed against when it has no PR (default origin/staging); '
                         'a branch with a PR is diffed against the PR base branch')
    ap.add_argument('--pr', action='append', default=[], type=int, metavar='N',
                    help='also show this PR (via gh), titled, against its own base branch; repeatable')
    ap.add_argument('--draft-prs', action='store_true',
                    help='also show every open draft PR (via gh), each against its own base branch')
    ap.add_argument('--code-ext', nargs='+', default=list(CODE_EXTS), metavar='EXT',
                    help=f'file extensions counted on the metrics tab (default: {" ".join(CODE_EXTS)})')
    ap.add_argument('--no-notes', action='store_true',
                    help='skip the per file change notes and the card summaries '
                         '(change_notes.py calls Claude for commits not cached yet)')
    ap.add_argument('-o', '--output', help='output HTML (default: delta.html next to the first JSON)')
    a = ap.parse_args()

    views = [DiagramView(p) for p in a.graphs]
    roots = {v.links.root for v in views}
    if len(roots) != 1:
        fail(f'graphs describe different repos: {sorted(roots)}')
    root = roots.pop()
    base_url = views[0].links.base
    branches = [branch_delta(root, ref, a.against) for ref in a.branch]
    branches += [pr_delta(root, gh_or_fail(root, 'pr', 'view', str(n), '--json', PR_FIELDS), a.against) for n in a.pr]
    if a.draft_prs:
        branches += [pr_delta(root, p, a.against) for p in
                     gh_or_fail(root, 'pr', 'list', '--draft', '--state', 'open', '--json', PR_FIELDS)]
    tops = branches + first_parent_commits(root, a.ref, a.n)
    kids = {c.key: c.children(root) for c in tops}
    everything = tops + [k for c in tops for k in kids[c.key][0]]
    by_merge, prs_note = merged_prs(root)
    attach_prs(everything, by_merge, prs_note)
    for c in everything:  # a merge's or branch's own commit messages say why its files changed
        c.child_messages = c.inner_messages(root)
    out = a.output or os.path.join(os.path.dirname(os.path.abspath(a.graphs[0])), 'delta.html')
    notes = summaries = None
    if not a.no_notes:
        notes = ChangeNotes(os.path.splitext(out)[0] + '_notes.json')
        notes.fill(everything)
        # After the notes: a summary reads its entry's per file notes.
        summaries = CommitSummaries(os.path.splitext(out)[0] + '_summaries.json', notes)
        summaries.fill(everything)
    commits = [as_entry(c, *kids[c.key], views, base_url, notes, summaries) for c in tops]

    data = {'repo': os.path.basename(root), 'root': os.path.abspath(root), 'ref': a.ref, 'base': base_url,
            'diagrams': [{'name': v.name, 'svg': v.svg, 'nodes': v.nodes} for v in views],
            'prs_note': prs_note, 'commits': commits, 'metrics': collect_metrics(root, tops, a.code_ext)}
    template = open(os.path.join(os.path.dirname(__file__), 'delta_viewer.html')).read()
    payload = json.dumps(data).replace('</', '<\\/')
    open(out, 'w', encoding='utf-8').write(template.replace('/*DATA*/null', payload))
    n_kids = sum(len(c['children']) for c in commits)
    print(f'{out}: {len(branches)} branch(es), {len(commits) - len(branches)} commits '
          f'({n_kids} listed inside), {len(views)} diagram(s)')


if __name__ == '__main__':
    main()
