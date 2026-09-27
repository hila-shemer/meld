# Responsiveness benchmarks

A small harness for measuring how Meld feels on big inputs: how long an
operation takes, and how long the main loop is blocked while it runs (a
blocked main loop is a window that does not redraw or react to input).

`meldbench.py` runs Meld in-process, opens a comparison and drives it
through a series of steps. A 2ms `G_PRIORITY_HIGH` timeout records every
gap between its dispatches; `max_block_ms` is the longest gap and
`stalled_ms` the total time spent in gaps of 50ms or more.

## Running

GTK needs a display. `in-container` runs a command in a Docker image with
the GTK 4 stack and Xvfb, as the calling user, with the repository and
`~/.cache/bld/meld` (tmpfs; test trees and results) mounted:

```sh
docker build --network host -t meld-bench -f bench/Containerfile bench
bench/mktree.py ~/.cache/bld/meld/syn2k --files 2000     # small sample first
bench/in-container python3 bench/meldbench.py \
    ~/.cache/bld/meld/syn2k/left ~/.cache/bld/meld/syn2k/right --out before.json
bench/compare.py before.json after.json
```

`--steps` picks a subset of `scan,expand,scroll,cursor,nextchange,filter,rescan`.
For profiling, wrap the run in `py-spy record -f raw -o prof.txt --` and
summarise with `bench/profsummary.py prof.txt`.

## Folder comparison steps

| step | what it does |
|---|---|
| scan | open the comparison; `first_row_ms` is when the first child row appears |
| expand_all / collapse_all / re_expand | folder expand/collapse on the root row |
| scroll_page, scroll_ends | page through the expanded tree; jump to either end |
| cursor_down, cursor_pgdn | move the cursor, waiting for a frame after each move |
| next_change | the "next change" action |
| filter_hide_same / filter_show_same | toggle the "same" status filter (a rescan) |
| rescan | refresh after appending to `--touch FILE` |

Test trees: `mktree.py` makes deterministic synthetic pairs; for a
realistic tree, two adjacent kernel releases (e.g. 6.12 and 6.12.1) differ
in a handful of files out of ~92k.
