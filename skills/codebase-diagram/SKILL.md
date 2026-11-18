---
name: codebase-diagram
description: Generate an editable draw.io architecture or data-flow diagram of a codebase, laid out with Graphviz and rendered to SVG/PNG. Use when asked to diagram, map, or visualize how a repo fits together, what calls what, or where the data goes.
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/check_deps.py *) Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/build_diagram.py *)
---

You survey the repo and write one graph JSON file. The bundled scripts do the
layout, the draw.io emission and the rendering. Never hand-write `.drawio` XML
or re-implement any part of the pipeline.

## Pipeline

```
graph.json  →  build_diagram.py  →  graph.drawio  →  graph.svg + graph.png
   (you)         (Graphviz dot)      (editable)       (--render)
```

Run once per repo, before the first build:

```
python3 ${CLAUDE_SKILL_DIR}/scripts/check_deps.py
```

Stop and report if it exits non-zero. If `defusedxml` is missing only inside
the default interpreter, retry the commands below with
`micromamba run -n py311 python`.

## Steps

1. **Survey.** Find the entry points, then follow one unit of work end to end
   (one request, one file, one message). Read the files you name; do not guess
   at what a module does from its filename. Aim for 25-50 nodes: a diagram
   nobody can read is worse than no diagram.

2. **Decide the bands.** Bands are the stages that unit of work passes through,
   in order. They are the backbone of the layout, so settle them before writing
   nodes. 4-9 bands is the usable range.

3. **Write the graph JSON** to `docs/diagram/<name>.json` in the target repo.
   Read `${CLAUDE_SKILL_DIR}/reference/graph-schema.md` for every field, then
   follow the authoring rules below. `${CLAUDE_SKILL_DIR}/examples/` holds a
   complete worked graph.

4. **Validate**, and fix what it reports:

   ```
   python3 ${CLAUDE_SKILL_DIR}/scripts/build_diagram.py docs/diagram/<name>.json --check
   ```

5. **Build and render:**

   ```
   python3 ${CLAUDE_SKILL_DIR}/scripts/build_diagram.py docs/diagram/<name>.json --render
   ```

6. **Look at the PNG** with the Read tool. This step is not optional: layout
   problems are only visible in the image. Check that bands run in order, that
   no box sits on top of another, that arrow direction matches the flow you
   described, and that labels are not clipped. Fix the JSON and rebuild.
   Layout fixes belong in the JSON's `config`, never in the `.drawio`, which is
   overwritten on every build.

7. **Check the arrows against the code:**

   ```
   python3 ${CLAUDE_SKILL_DIR}/scripts/check_edges.py docs/diagram/<name>.json [--ref origin/main]
   ```

   It reduces the repo's import graph to the boxes (files without a box are
   passed through) and lists imports with no arrow or ✳ chip, and arrows
   between code boxes with no import path either way. Work through the
   **direct imports** list: each shows what is imported, so decide whether it is
   the step the box stands for (draw it) or an incidental helper (leave it).
   Long chains through files with no box are mostly incidental; read the
   shortest ones. For each "drawn, no import" arrow, confirm the link is made
   another way (HTTP call, table, queue, job name) or remove it. Tables,
   external services and HTTP routes are not checked by imports: check those
   by reading. `--ref` reads the code at a git ref instead of the working tree.

8. **Report** the output paths and the band structure. Commit the `.json`,
   `.drawio`, `.svg` and `.png` together if the user wants them committed.

## Authoring rules

- One node is one file, one directory, or one external service. If a node has
  no `file`, justify why it exists.
- `desc` is a hover tooltip and the main payload of the diagram. Two or three
  sentences on what this step actually does, written from having read the code.
  No filler, no restating the label.
- `lbl` is short: a name and at most a parenthetical. The file path is added
  underneath automatically.
- Edge `label` names the thing handed over (`metadata.json`, `access_token`),
  not the act of handing it (`sends`, `calls`).
- Set `robot: true` on any node that calls an LLM. It gets a 🤖 prefix.
- Every node's `grp` is a band key or `"ext"`. External services float beside
  the bands; give them `type: "ext"` too.
- Draw the arrows that exist, including ones that run backwards against the
  band order. The builder relaxes rank on those instead of reordering the page.
- Shared nodes use the ✳ proxy convention (below) instead of long arrows.

## Shared nodes: the ✳ proxy convention

A node that many boxes use (a secrets loader, an API client, a file holding
every prompt) draws a bundle of long lines across the page, and the reader
loses which box uses it. Draw it once, mark it ✳, and put a small chip beside
each user instead.

Use it when a node has 4 or more users, or users in bands far from its own.
Every user gets a chip; leave no direct arrow from the shared node to a user.

Tables (`data` nodes) are the exception: draw a plain arrow to a user in the
table's own band or the next band, and a chip only for a user further away.
A diagram where every table is a ✳ grows a cloud of chips around each box;
one where every table use is an arrow is a web of long dashed lines.

Keep it readable over complete: about 60 to 90 plain arrows, and no box
sending more than about 6 arrows across bands. When check_edges lists more
real imports than that allows, draw the ones that carry the main work and
leave the rest in the box's `desc`.

- **The shared node** stays in its home band, unchanged.
- **The ✳ marker:** a `note` node `STAR_<name>`, `lbl` `"✳"`, `w` 40, `h` 32,
  in the shared node's band, with a plain edge (no `kind`, no label) from the
  shared node to it. Its `desc` names the convention and says why this node
  uses it: who the users are and where they sit. Name users the diagram does
  not draw too, so the list is complete.
- **One chip per user:** a `proxy` node `SX_<name>_<user>` in the user's band,
  `lbl` `"<shared name> ✳"`, `proxy_of` set to the shared node's id, and no
  `file` of its own: the chip takes the shared node's file, so it links to the
  same place and lights up with it in the delta viewer. `desc` starts `Proxy for <file> (see the ✳ node):` and says what this user
  takes from it.
- **Chip edge:** `kind: "proxy"`, between the chip and the user, in the
  direction the data moves: chip to user when the shared node supplies
  something, user to chip when the user calls into it. `label` names what is
  handed over, as for any edge.

## Delta viewer: commits and PRs on the diagrams

`delta_viewer.py` writes one self-contained HTML page (default
`docs/diagram/delta.html`) that draws what recent commits and open PRs changed
on the built diagrams. It only reads git and gh; build the diagrams first.

```
git fetch
python3 ${CLAUDE_SKILL_DIR}/scripts/delta_viewer.py docs/diagram/<a>.json [docs/diagram/<b>.json ...] \
    [--ref origin/main] [-n 5] [--branch origin/<branch> ...] [--pr N ...] [--draft-prs] [--no-notes]
```

- **List (left):** the last `-n` first parent commits of `--ref`, and above them
  each `--branch`, `--pr` or open draft PR, diffed against its PR's base branch
  (with no PR, against `--against`, default `origin/staging`; the card says so).
  A merge or branch lists the commits inside it.
- **PR view (header toggle):** one card per pull request, titled with the PR
  title. A PR's own commits are folded into its card, so its diff, file notes
  and summary cover the whole PR (merge commit against its first parent, or the
  branch against its base). A release PR that merged other PRs still lists
  those PRs. Merged PRs are matched to their merge or squash commit with one
  `gh pr list` call; if gh fails the build warns and the cards say "PR text not
  read".
- **Diagram (middle):** boxes whose `file` the picked entry touched are
  outlined (green added, amber modified, red deleted) with +/- line counts; the
  rest fade. "Diff: off" shows the plain diagram. Hovering a touched box shows
  what changed in each of its files and why.
- **Detail (right):** message, PR link and description, the changed files
  grouped by box (files no box covers are listed too), and each file's diff.
- **Code metrics tab:** complexity, file count, lines of code and file lengths
  at each listed entry (lizard).
- **Notes and summaries:** a note per changed file and a 2 to 4 sentence summary
  per card, written by `claude -p` (`change_notes.py`, Claude Code login, not
  API credits) and cached in `delta_notes.json` and `delta_summaries.json` next
  to the page, so a rebuild only asks about new commits. A PR's summary is
  rewritten when its title or description changes. Failures are reported on
  stderr and on the page. `--no-notes` skips both.

When the page lists changed files that no box covers, add boxes for them to the
graph JSON and rebuild, so every changed file lights a box.

## Repo links

Nodes link to GitHub automatically from their `file` path. The builder derives
the repo URL and branch from `git remote` and only links paths that `git
ls-files` reports, so ignored files and per-user templates stay unlinked. Set
`config.github` only to override that.
