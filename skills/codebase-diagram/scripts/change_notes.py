#!/usr/bin/env python3
"""
change_notes.py: a short note per changed file per commit (what changed in the
file, and why), written by Claude from the commit messages and the diff. The
delta viewer shows the note when the mouse is on a box in diff mode.

One `claude -p` call (Claude Code login, not API credits) per commit, or per batch of
files for a big commit, writes the notes; files the model skips are asked again.
Notes are cached in a JSON file next to the page (key: base..sha), so each
commit is summarised once; a rebuild only calls Claude for new commits.
A failed call is reported on stderr and leaves that commit without notes; it
is retried on the next build.

CommitSummaries does the same for whole entries: 2 to 4 sentences per commit,
merged PR or branch (what it does and why), from its messages, PR title and
body, per file notes and diff, shown when the mouse or keyboard focus is on its
card in the list. A merged PR's summary covers its whole diff (merge commit
against its first parent), so the page's PR view shows it for the PR.
"""
import hashlib, json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

MODEL = 'claude-opus-5-5'
PATCH_LINES = 150          # lines of one file's patch sent to the model
PROMPT_CHARS = 120_000     # patch text in one call; a bigger commit is split over several calls
BATCH_FILES = 40           # files in one call
NO_PATCH = ('pnpm-lock.yaml', 'package-lock.json', 'yarn.lock', '.png', '.svg', '.drawio', '.parquet')
SYSTEM = ('You write short notes for a code review page. For each changed file you get, write what changed '
          'in that file and why, in 2 or 3 plain sentences. Say what the code now does differently, not how '
          'the diff looks. Take the why from the commit messages. No PR or issue numbers, no markdown, no '
          'hyphens or dashes used as punctuation. Each file is headed with its number in square brackets. Reply '
          'with one JSON object mapping each file number (as a string) to its note, and nothing else.')


# Runs on the Claude Code login (claude.ai subscription), never on API credits:
# without an API key in its environment, claude -p can only use the login.
CLAUDE_LOGIN_ENV = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}


def newest_claude():
    """The newest `claude` on PATH. `micromamba run -n py311` puts the env's own, older
    Claude Code first, and that one refuses newer models."""
    found = {}
    for d in os.environ.get('PATH', '').split(os.pathsep):
        p = os.path.join(d, 'claude')
        if not os.access(p, os.X_OK):
            continue
        r = subprocess.run([p, '--version'], capture_output=True, text=True, timeout=60)
        m = re.match(r'(\d+)\.(\d+)\.(\d+)', r.stdout.strip())
        if m:
            found.setdefault(tuple(map(int, m.groups())), p)
    if not found:
        raise SystemExit('change notes: no claude on PATH; build with --no-notes to skip notes')
    v = max(found)
    print(f'change notes: using {found[v]} ({".".join(map(str, v))})', file=sys.stderr)
    return found[v]


def cache_key(c):
    return f'{c.base}..{c.sha}'


def ask_claude(claude, system, prompt, cwd):
    """One `claude -p` call that must answer with a JSON object; returns that object.
    Raises on a failed call or a reply without JSON."""
    r = subprocess.run(
        [claude, '-p', '--model', MODEL, '--system-prompt', system, '--no-session-persistence',
         '--output-format', 'json', '--disallowedTools', 'Bash Edit Write Read Grep Glob WebFetch WebSearch Agent'],
        input=prompt, capture_output=True, text=True, timeout=600, cwd=cwd, env=CLAUDE_LOGIN_ENV)
    out = json.loads(r.stdout)
    if out.get('is_error'):
        raise RuntimeError(out.get('result'))
    text = out['result']
    return json.loads(text[text.index('{'):text.rindex('}') + 1])


def pack(items, size, max_items, max_chars):
    """Split items into calls of at most max_items items and about max_chars of size(item) text."""
    out, cur, total = [], [], 0
    for it in items:
        n = size(it)
        if cur and (len(cur) >= max_items or total + n > max_chars):
            out.append(cur)
            cur, total = [], 0
        cur.append(it)
        total += n
    return out + [cur] if cur else out


def by_number(reply, items):
    """A reply keyed by item number ("1", "2", ...) as {item index: text}. Keyed by number,
    not name: the model sometimes miscopies a path or hash."""
    return {int(k) - 1: str(v) for k, v in reply.items() if str(k).isdigit() and 1 <= int(k) <= len(items)}


class ClaudeCache:
    """Model written texts cached in a JSON file (`path`), filled in rounds of parallel
    `claude -p` calls. A subclass says what is missing (_jobs), how to ask (_ask) and
    where the answer goes (_take)."""
    what = 'texts'

    def __init__(self, path):
        self.path = path
        self.cache = json.load(open(path)) if os.path.exists(path) else {}
        self.errors = {}  # cache key -> why this build has no text for it; shown on the page
        self.claude = None

    def fill(self, commits, workers=4, rounds=3):
        """Ask for everything not in the cache yet; whatever the model skips is asked again next round."""
        commits = list({self._key(c): c for c in commits}.values())  # a commit listed twice is asked once
        for rnd in range(1, rounds + 1):
            jobs = self._jobs(commits)
            if not jobs:
                break
            if self.claude is None:
                self.claude = newest_claude()
            print(f'{self.what}: round {rnd}: asking {MODEL} in {len(jobs)} call(s)', file=sys.stderr)
            with ThreadPoolExecutor(workers) as pool:
                for job, res in zip(jobs, pool.map(self._ask_logged, jobs)):
                    self._take(job, res)
                    self._save()
        self._report(commits, rounds)
        if self.errors:
            print(f'{self.what}: missing for {len(self.errors)} commit(s); the page says so where they would show',
                  file=sys.stderr)

    def _ask_logged(self, job):
        """(reply by item index, '') or (None, error). A failure is reported, not hidden: those
        items show the error on the page and are asked again on the next build."""
        try:
            return by_number(ask_claude(self.claude, self.system, self._prompt(job), os.path.dirname(self.path)),
                             self._items(job)), ''
        except Exception as e:
            print(f'{self.what}: FAILED for {self._label(job)}: {e}', file=sys.stderr)
            return None, str(e)[:300]

    def error_for(self, c):
        return self.errors.get(self._key(c), '')

    def _save(self):
        tmp = self.path + '.tmp'
        json.dump(self.cache, open(tmp, 'w'), indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)


class ChangeNotes(ClaudeCache):
    """A note per changed file per commit, keyed base..sha then path. Big commits go in several
    calls (BATCH_FILES, PROMPT_CHARS)."""
    what = 'change notes'
    system = SYSTEM

    @staticmethod
    def _key(c):
        return cache_key(c)

    def _missing(self, c):
        have = self.cache.get(cache_key(c), {})
        return [f for f in c.files if f['path'] not in have]

    def _jobs(self, commits):
        return [(c, batch) for c in commits
                for batch in pack(self._missing(c), lambda f: len(_patch_text(f)), BATCH_FILES, PROMPT_CHARS)]

    @staticmethod
    def _items(job):
        return job[1]

    @staticmethod
    def _label(job):
        return f'{job[0].sha[:7]} {job[0].subject[:60]!r}'

    def _take(self, job, res):
        (c, files), (got, err) = job, res
        if got is None:
            self.errors[cache_key(c)] = err
            return
        notes = {files[i]['path']: n for i, n in got.items()}
        missing = [f['path'] for f in files if f['path'] not in notes]
        if missing:
            print(f'change notes: {c.sha[:7]} has no note for {len(missing)} file(s): {missing[:5]}', file=sys.stderr)
        self.cache.setdefault(cache_key(c), {}).update(notes)

    def _report(self, commits, rounds):
        for c in commits:
            left = self._missing(c)
            if left and cache_key(c) not in self.errors:
                self.errors[cache_key(c)] = f'the model gave no note for {len(left)} file(s) in {rounds} tries'

    def for_commit(self, c):
        return self.cache.get(cache_key(c), {})

    @staticmethod
    def _prompt(job):
        c, files = job
        parts = [f'Commit: {c.subject}', c.body.strip()]
        kids = getattr(c, 'child_messages', [])
        if kids:
            parts.append('Commits it contains:\n' + '\n\n'.join(kids))
        others = len(c.files) - len(files)
        parts.append(f'Write a note for each of these {len(files)} changed files'
                     + (f' ({others} other files of this commit are covered elsewhere)' if others else '') + ':')
        for i, f in enumerate(files, 1):
            parts.append(f"\n=== [{i}] {f['path']} ({f['status']}, +{f['add']} -{f['del']})\n{_patch_text(f)}")
        return '\n'.join(parts)


SUMMARY_SYSTEM = (
    'You write short summaries for a code review page. You get numbered entries; each is one commit, one '
    'merged pull request or one whole branch not merged yet. For each, write what it does and why in 2 to 4 plain sentences, for a reader '
    'who knows the product but not the code. Say what changes for the app, its users or its developers, not how '
    'the diff looks. Take the why from the commit messages and the pull request text. No PR or issue numbers, '
    'no commit hashes, no markdown, no hyphens or dashes used as punctuation. Reply with one JSON object mapping '
    'each entry number (as a string) to its summary, and nothing else.')
ENTRY_CHARS = 30_000       # text about one entry in a summary call
ENTRY_MESSAGES = 40        # commit messages of a branch or merge sent for its summary
ENTRIES_PER_CALL = 6


def summary_key(c):
    """Commit: base..sha. Branch or merged PR: also a hash of its PR title and body, so
    editing the PR text writes a new summary."""
    pr = getattr(c, 'pr', None)
    if not pr:
        return cache_key(c)
    return f"{cache_key(c)}#pr:{hashlib.sha1((pr['title'] + pr['body']).encode()).hexdigest()[:10]}"


class CommitSummaries(ClaudeCache):
    """2 to 4 sentences per commit or branch, keyed by summary_key. Written from the commit
    message, the branch's commit messages and PR body, the per file notes (`notes`) and the diff."""
    what = 'summaries'
    system = SUMMARY_SYSTEM

    def __init__(self, path, notes=None):
        super().__init__(path)
        self.notes = notes

    @staticmethod
    def _key(c):
        return summary_key(c)

    def _jobs(self, commits):
        todo = [c for c in commits if summary_key(c) not in self.cache]
        return pack(todo, lambda c: len(self._entry(c, 0)), ENTRIES_PER_CALL, PROMPT_CHARS)

    @staticmethod
    def _items(job):
        return job

    @staticmethod
    def _label(job):
        return ', '.join(c.sha[:7] for c in job)

    def _take(self, job, res):
        got, err = res
        for i, c in enumerate(job):
            if got is None:
                self.errors[summary_key(c)] = err
            elif i in got:
                self.cache[summary_key(c)] = got[i]

    def _report(self, commits, rounds):
        for c in commits:
            if summary_key(c) not in self.cache and summary_key(c) not in self.errors:
                self.errors[summary_key(c)] = f'the model gave no summary in {rounds} tries'

    def for_commit(self, c):
        return self.cache.get(summary_key(c), '')

    def _prompt(self, job):
        return '\n\n'.join(self._entry(c, i) for i, c in enumerate(job, 1))

    def _entry(self, c, i):
        """What the model reads about one entry, at most about ENTRY_CHARS: messages first, then
        the per file notes, then as much diff as fits."""
        pr = getattr(c, 'pr', None)
        kind = 'Branch not merged yet' if c.key.startswith('branch:') else 'Merged pull request' if pr else 'Commit'
        parts = [f"=== [{i}] {kind}: {c.subject}", c.body.strip()]
        if pr:
            parts.append(f"Pull request: {pr['title']}\n{pr['body'].strip()}")
        kids = getattr(c, 'child_messages', [])
        if kids:
            more = f'\n({len(kids) - ENTRY_MESSAGES} older commits not shown)' if len(kids) > ENTRY_MESSAGES else ''
            parts.append('Commits it contains:\n' + '\n\n'.join(kids[:ENTRY_MESSAGES]) + more)
        notes = self.notes.for_commit(c) if self.notes else {}
        parts.append('Changed files:\n' + '\n'.join(
            f"{f['path']} ({f['status']}, +{f['add']} -{f['del']})" + (f": {notes[f['path']]}" if f['path'] in notes else '')
            for f in c.files))
        text = '\n'.join(p for p in parts if p)[:ENTRY_CHARS]
        diff, left = [], ENTRY_CHARS - len(text)
        for f in c.files:
            patch = _patch_text(f)
            if patch and len(patch) < left:
                diff.append(patch)
                left -= len(patch)
        shown = f'Diff ({len(diff)} of {len(c.files)} files shown):\n' if diff else ''
        return text + ('\n' + shown + '\n'.join(diff) if diff else '')


def _patch_text(f):
    """The patch text sent for one file: none for binaries and lockfiles, else its first PATCH_LINES lines."""
    if f['binary'] or f['path'].endswith(NO_PATCH):
        return ''
    return '\n'.join(f['patch'].split('\n')[:PATCH_LINES])
