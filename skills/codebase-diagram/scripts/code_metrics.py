"""
code_metrics.py: size and cyclomatic complexity of a repo's code at given
commits, for the delta viewer's metrics tab.

Counts tracked files whose extension is in CODE_EXTS. Lines are lizard's NLOC:
lines of code, without blank and comment lines. Complexity is the sum of
lizard's cyclomatic complexity number (CCN) over every function: 1 per
function plus 1 per branch (if, loop, case, &&, ||, ?:, catch).

Each file version (git blob) is read and analysed once, so commits that share
most of their files cost little.
"""
import math, statistics, subprocess
import lizard
from build_diagram import git, fail

CODE_EXTS = ('.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.py')


class BlobReader:
    """Reads blob contents through one `git cat-file --batch` process."""

    def __init__(self, root):
        self.proc = subprocess.Popen(['git', '-C', root, 'cat-file', '--batch'],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, sha):
        self.proc.stdin.write(sha.encode() + b'\n')
        self.proc.stdin.flush()
        head = self.proc.stdout.readline().split()
        if len(head) != 3 or head[1] != b'blob':
            fail(f'git cat-file: {sha} is not a blob ({b" ".join(head).decode()})')
        data = self.proc.stdout.read(int(head[2]))
        self.proc.stdout.read(1)  # newline after the contents
        return data.decode('utf-8', errors='replace')

    def close(self):
        self.proc.stdin.close()
        self.proc.wait()


class CodeMetrics:
    """Metrics of the code files at one commit; per blob results cached."""

    def __init__(self, root, exts=CODE_EXTS):
        self.root, self.exts = root, tuple(exts)
        self.blobs = BlobReader(root)
        self.cache = {}  # blob sha -> (nloc, ccn, functions)

    def _code_files(self, sha):
        """(path, blob sha) of every tracked code file at this commit."""
        out = []
        for rec in git(self.root, 'ls-tree', '-r', '-z', sha).split('\0'):
            if not rec:
                continue
            meta, path = rec.split('\t', 1)
            mode, kind, blob = meta.split()
            if kind == 'blob' and mode != '120000' and path.endswith(self.exts):
                out.append((path, blob))
        if not out:
            fail(f'no code files ({", ".join(self.exts)}) at {sha[:7]} in {self.root}')
        return out

    def _analyse(self, path, blob):
        if blob not in self.cache:
            info = lizard.analyze_file.analyze_source_code(path, self.blobs.read(blob))
            funcs = info.function_list
            self.cache[blob] = (info.nloc, sum(f.cyclomatic_complexity for f in funcs), len(funcs))
        return self.cache[blob]

    def at(self, sha):
        """Metrics dict for the commit `sha`."""
        rows = [(path, *self._analyse(path, blob)) for path, blob in self._code_files(sha)]
        lengths = sorted(r[1] for r in rows)
        longest = max(rows, key=lambda r: r[1])
        top = lengths[-math.ceil(len(lengths) / 10):]
        functions = sum(r[3] for r in rows)
        ccn = sum(r[2] for r in rows)
        return {'files': len(rows), 'loc': sum(lengths),
                'avg': round(sum(lengths) / len(rows), 1),
                'max': longest[1], 'max_path': longest[0],
                'p90': round(statistics.quantiles(lengths, n=10, method='inclusive')[-1], 1),
                'top10_n': len(top), 'top10_avg': round(sum(top) / len(top), 1),
                'ccn': ccn, 'functions': functions,
                'ccn_avg': round(ccn / functions, 2) if functions else 0}

    def close(self):
        self.blobs.close()
