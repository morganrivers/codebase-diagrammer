# Graph JSON schema

One file, three keys: `config`, `nodes`, `edges`.

```json
{
  "config": { "...": "..." },
  "nodes": [ { "id": "app", "grp": "I", "type": "script", "lbl": "app.py\n(Streamlit host)",
               "file": "webapp/app.py", "desc": "Streamlit entrypoint. ..." } ],
  "edges": [ { "from": "app", "to": "loader", "label": "startup", "kind": "nav" } ]
}
```

## config

| key | default | meaning |
| --- | --- | --- |
| `title` | `"diagram"` | draw.io page name |
| `id` | `"diagram"` | draw.io diagram id |
| `bands` | required | ordered list of `{key, label, palette}`, first band drawn at the bottom under the default `rankdir: "BT"` |
| `band_label` | `"lane"` | `"lane"` tints a full-width stripe and names it at the left margin; `"pill"` centres a coloured tag above each band |
| `rankdir` | `"BT"` | `"BT"` reads bottom to top, `"TB"` top to bottom |
| `nodesep` | `0.45` | Graphviz separation within a rank |
| `ranksep` | `0.85` | Graphviz separation between ranks |
| `show_paths` | `true` | append each node's `file` under its label |
| `relax_backward_edges` | `true` | an edge running against band order does not set rank |
| `funnel_weight` | `0` | weight of the invisible band-ordering edges; raise only to pull bands into a column |
| `edge_kinds` | `{}` | extra or overriding kinds, merged over the defaults |
| `github` | `{}` | see below |
| `page_width` / `page_height` | `1600` / `2400` | draw.io page size |

### config.github

| key | default | meaning |
| --- | --- | --- |
| `base` | from `git remote get-url origin` | repo URL, e.g. `https://github.com/owner/repo` |
| `branch` | current branch | branch the links point at |
| `root` | git top level | repo root, relative to the JSON file |
| `link_mode` | `"tracked"` | `"tracked"` links only paths `git ls-files` reports; `"trust"` links any path without template characters `{ [ < '`, for a graph kept outside the repo it describes |
| `label_link_dirs` | `[]` | directories scanned for filenames, so an edge label naming a data file links to it |

## nodes

| field | required | meaning |
| --- | --- | --- |
| `id` | yes | unique, referenced by edges |
| `lbl` | yes | short label; `\n` splits lines |
| `grp` | yes | a band `key`, or `"ext"` for anything outside the pipeline |
| `type` | no, default `script` | see below |
| `file` | no | repo-relative path; drives the GitHub link and the second label line |
| `desc` | no | hover tooltip; the real content of the diagram |
| `robot` | no | `true` prefixes 🤖, meaning this step calls an LLM |
| `link` | no | explicit URL, overriding the derived one |
| `w` / `h` | no | explicit pixel size, overriding label-driven sizing |

### node types

| type | shape |
| --- | --- |
| `script` | rounded box, coloured by its band |
| `data` | cylinder, for a file or table at rest |
| `ext` | blue pill, for a third-party service or model artifact |
| `danger` | red pill, for a step with a security consequence |
| `note` | yellow note, for an annotation with no traffic through it |
| `proxy` | small dashed box, for a repeat of a node drawn elsewhere |

## edges

| field | required | meaning |
| --- | --- | --- |
| `from` / `to` | yes | node ids |
| `label` | no | what is handed over |
| `kind` | no | styling class, below |
| `tip` | no | hover tooltip; first line renders bold |
| `link` | no | explicit URL, overriding the label-derived one |

### edge kinds

| kind | colour | use |
| --- | --- | --- |
| `flow`, `handoff` | green | the main path of the work |
| `loader` | blue dashed | something loaded at startup |
| `cache`, `store` | amber dashed | written to or read from disk |
| `mask` | heavy green | a boundary data is transformed across |
| `boot` | orange dashed | startup or attestation |
| `nav` | grey dashed | navigation or wiring, not data |
| `proxy` | grey dashed | link to a repeated node |
| `danger` | red dashed | a path with a security consequence |
| `""` | grey | unclassified |

Add project-specific kinds under `config.edge_kinds`, each
`{"color": "#rrggbb", "dashed": true, "width": 2}` with every field optional.

## palettes

`slate`, `teal`, `green`, `peach`, `lavender`, `grey`, `steel`. A band's
palette sets both its node fill and its band label colour.

## Layout notes

- Node size is computed from the label, so Graphviz reserves the real
  footprint and boxes do not overlap. Override with `w`/`h` only for chips.
- Bands are enforced with one invisible node per gap. Two classes of edge are
  exempted from setting rank: an `ext` node feeding more than one band, and
  (under `relax_backward_edges`) any edge running against band order.
- Edge waypoints come from Graphviz and are baked into the `.drawio`, so the
  file opens in draw.io already routed and stays editable.
