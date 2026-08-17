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

7. **Report** the output paths and the band structure. Commit the `.json`,
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

## Repo links

Nodes link to GitHub automatically from their `file` path. The builder derives
the repo URL and branch from `git remote` and only links paths that `git
ls-files` reports, so ignored files and per-user templates stay unlinked. Set
`config.github` only to override that.
