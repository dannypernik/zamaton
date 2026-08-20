## 2026-08-20 — generate.py: image consolidation cleanup, table fix, figure size, SLT labels, second-copy path

**State:** in-progress — all changes in `generate.py`, uncommitted (consistent with prior entries).

**What I did & why:**

### Image generator consolidation (completing prior session's plan)
Removed `IMAGE_CSS` (~250 lines) and `build_image_html` entirely. `render_one` now calls `build_worksheet_html` with a single question and screenshots `.question-block` instead of `#question-card`. One CSS source of truth (`_worksheet_css()`) for all three output modes. The question header (number + ID) is hidden and 24px padding added via `page.add_style_tag` in `render_one` only — worksheet rendering unaffected (`generate.py:926`).

### Table width fix
Prior session added `width: 100% !important` to fix squished tables on `1142af44`, `78391fcc`, `0366d965`, `1353b86e` (all had `style="width:100px;"` inline pixel values). `!important` was too blunt — it also overrode legitimate percentage-width tables like `94237701` (`style="width: 25%;"`, which was never broken). Fixed by reverting CSS to plain `width: 100%` and instead stripping inline pixel widths < 400px per-element in `_DOM_FIXUPS` JS, leaving percentage widths alone (`generate.py:856`).

### Figure size nudge
`figure.image img` height raised 130pt → 155pt, max-height 180pt → 210pt (both the `!important` override block and the base rule at `generate.py:630`, `generate.py:650`). Note: the aspect-ratio fix from 2026-08-12 (switching from fixed height to `height: auto + max-height`) must still apply on top of this.

### SLT worksheet labels use actual skill codes
`_render_slt_uniques` previously stored `(i, qid, q)` with an integer row index and used `label_prefix="SLT"` → produced labels like `SLT.1`, `SLT.2`. Changed to store `(code, qid, q)` where `code` is the raw `Skill code` field (e.g. `SLT5 RW 2.6`) and pass `label_prefix=""`. Updated `build_worksheet_html` to handle empty prefix: `label = str(q_num) if not prefix else f"{prefix}.{q_num}"` (`generate.py:858`). Answer key labelled list updated to match.

### Second save location for concept worksheets
`import shutil` added. Constants `_CONCEPTS_MATH`, `_CONCEPTS_RW` point into `/Users/danny/Projects/Open Path/...`. After each worksheet render, copies to `_CONCEPTS_{SUBJ}/{skill}/`; after each answer key, copies to `_CONCEPTS_{SUBJ}/Answers/`. `--keys-only` respected. `run_math_worksheets` (`generate.py:1675`) and `run_rw_worksheets` (`generate.py:1719`).

### Image fallback status check
Queried live spreadsheet vs. JSON banks: 31 math + 32 RW questions still missing from bank (still relying on image fallback or skipped). Worksheet questions with real IDs are convertible; `abcd*` placeholder IDs and live SAT/SLT questions need sourcing.

**Artifacts (referenced, not restated):**
- `generate.py:856` — `_DOM_FIXUPS` table-width strip
- `generate.py:858` — empty-prefix label handling
- `generate.py:926` — `render_one`, image consolidation
- `generate.py:630`, `generate.py:650` — figure height change
- `generate.py:1675`, `generate.py:1719` — second-copy logic

**Open questions / blockers:**
- None from this session.

**Next step:** Run a test generation to verify image output, second-location copies, and SLT labels. Then commit (per-feature as per CLAUDE.md).

## 2026-08-12 — generate.py: practice-test output restructure, two chart/figure rendering bugs found via user report, full regen

**State:** done — all changes made, verified against live data, affected outputs regenerated; `generate.py` still uncommitted (same convention as prior entries — this repo doesn't commit `generate.py` or `docs/` per-session).

**What I did & why:**

### Practice-test output restructure (feature request, iterated on user feedback)
Single-module PDFs now save to `SAT#/Modules/` instead of flat in `SAT#/` (`_practice_test_worker`, generate.py:2215). Every module PDF gets a matching `~Key.pdf` answer key, and the combined `SAT#.pdf` gets a combined key too (concatenated from module keys, mirroring how the combined PDF itself is built) — `_render_module_pdf` (generate.py:2160) now renders both. First pass put keys in `_Answers` subfolders (matching the worksheet convention); user corrected — for practice tests they want keys sitting in the *same* directory as their PDF, no `_Answers` split. `load_test_data()` (generate.py:2118) now takes `master_rows` as a param instead of fetching its own, so `run_practice_tests` can reuse one fetch for both test-data building and the new `sheet_answers` map the keys need.

### Bug 1: SVG legend-chart corruption, found via user-reported `cbdd5287`
`_move_svg_legend_right()` (generate.py:280) repositions a chart's below-chart legend to the right. Its element-classifier regex (generate.py:355) tried to close self-closed tags (`<line/>`, `<circle/>`, etc.) with an *optional* lazy match against `</line|rect|text|...>` — since only `<text>` has a real closing tag among that set, the lazy match would skip past several unrelated self-closed siblings and glue onto the next `</text>` it found, merging elements and dropping the final `>` off ones it didn't merge. Reassembled with `"".join(...)`, this produced malformed unterminated SVG that browsers silently drop — chart lines and legend markers vanished. Fixed by requiring either a real self-close (`/>`) or an open tag closed by *the same* tag name via backreference. Verified structurally (0/62 gaps) and via full pipeline render (screenshot + `SendUserFile`).

**Scope was much bigger than the one reported ID:** re-ran the old buggy regex against every chart SVG in both banks — **all 62** RW "Command of Evidence" chart questions were affected (0 in math; math has no charts using this legend pattern), to varying degrees. Full ID list in conversation history, not restated here.

### Bug 2: raster figure aspect-ratio squish, found via user-reported `4c95c7d4`
`figure.image img` (generate.py:630, 650) forced a fixed `height: 155pt !important` while a separate rule clamped `max-width: 70%` (not `!important`). For a wide/landscape raster fallback image (CollegeBoard-style `<div class="standalone_image">`, promoted to `figure.image` by `prep_html`), the browser computes width from the fixed height via intrinsic ratio, *then* clamps that width to the 70% cap without adjusting the now-mismatched height — silent squish, no error. User caught this immediately on the first two other IDs I listed ("aspect ratios squished — that should never happen"). Fixed by switching to `height: auto` + `max-height: 155pt`, mirroring the pattern `figure.image svg` already used correctly (both dimensions `auto`, both capped → standard CSS "contain" sizing, ratio always preserved).

Scanned both banks for the same raster-fallback pattern (60 candidates, all math, 0 in RW); ranked by projected width at 155pt height. 5 exceed the 70% cap and were confirmed squished pre-fix: `b680e76d`, `fa7a0164`, `374b18f9`, `1c3d613c`, `4c95c7d4`. Post-fix, verified via full-set programmatic check (rendered aspect ratio vs. native PNG aspect ratio, Playwright headless) — **0/60 mismatches**, including the 55 non-offending ones (confirms no regression, not just that the 5 got fixed).

### Full regen after both fixes
`generate.py images --ids <67 combined> --regen-worksheets` (67/67 images; math worksheets Probability·Easy / Distributions·Easy / Lines-Angles-Triangles·Medium / Right-Triangles·Hard; RW worksheets Command of Evidence·1/2/3 — all + answer keys), then `generate.py practice-tests` for all 10 tests (every one contained at least one affected Command of Evidence question via SAT#/PSAT# skill codes; SAT1-3 aren't generated by this pipeline at all, so were skipped even though a few affected IDs also carry SAT1-3 codes). Spot-verified `cbdd5287` live inside the real `SAT8/Modules/SAT8 - Reading & Writing Module 3.pdf` output, not just an isolated render.

### Not adopted — open experiment
Built a scratch preview (`_preview_coe2.py`, deleted after use — not committed) showing "Command of Evidence 2" with question/answer text at 9pt (from 10pt) and figures ~16% bigger (max-width 70%→78%). Sent to user as two page images + the PDF for review. **User has not said whether to adopt this** — it was a "what if" preview only, `generate.py`'s actual CSS is unchanged by it.

**Artifacts (referenced, not restated):**
- `generate.py:280` — `_move_svg_legend_right`, regex fix
- `generate.py:630`, `generate.py:650` — `figure.image img`, aspect-ratio fix
- `generate.py:2118`, `generate.py:2160`, `generate.py:2215` — practice-test restructure
- `question-images/`, `math-worksheets/`, `rw-worksheets/`, `practice-tests/` — regenerated outputs (all gitignored, not committed)

**Open questions / blockers:**
- Command of Evidence 2 font/figure-size preview: adopt, tweak, or drop? Waiting on user.
- Neither bug fix nor the restructure has been committed (matches this repo's established no-commit-per-session pattern for `generate.py` — flag if that assumption is ever wrong).

**Next step:** Wait for user direction on the font/figure-size preview; otherwise no pending `generate.py` work.

## 2026-08-07 — generate.py: SLT worksheets, tombstoning fixes, live data sourcing, filename/CSS fixes

**State:** done — all changes made and regenerated against live data; `generate.py` still uncommitted (matches prior entry's convention — this repo doesn't seem to commit generate.py per-session).

**What I did & why:**

### Italic → MathML conversion
`<span class="italic">` had no CSS rule, so math variables rendered upright. `_italic_span_to_mi()` (generate.py:178) converts these to `<math><mi>…</mi></math>` per-character (MathML only italicizes single-char `<mi>`), gated by a new `math_mode` param threaded through `clean_html`/`prep_html`/`build_worksheet_html` so RW content (which uses the same pipeline but should never get MathML-ified) is untouched.

### SLT Uniques worksheets (new feature, iterated hard)
Built `_render_slt_uniques()` (generate.py:1978) — one combined worksheet + key per subject from every master-spreadsheet row whose Skill code starts with `SLT`, in spreadsheet order. Wired into `run_math_worksheets`/`run_rw_worksheets` so a normal `math`/`rw` run produces these automatically (skipped when `--skill` filters to one topic). `run_slt_worksheets()` (generate.py:2070) remains as a standalone command for regenerating just these two.

**Subject-splitting saga (important — don't redo this mistake):** tried filtering SLT rows by the Skill code's embedded RW/M token per user instruction. Validated against actual JSON bank membership and found the *code token* disagrees with real content in 84/177 SLT rows, while the *Subject column* had 0 disagreements — reverted to Subject-column filtering. Then the user's own spreadsheet fix flipped this (made Subject match the wrong/code-derived value for those same 84 rows); re-validated, confirmed, and the user hand-corrected the sheet again. Lesson: when a spreadsheet column and a derived value disagree, cross-check against the JSON bank (ground truth for "what is this question, actually") before trusting either column.

### Tombstoning made SLT-aware
Concept worksheets hide (tombstone) questions that also appear on a live/unreleased test. `_test_code_status()` (generate.py:68) classifies a Skill code's leading token as `published` (SAT1-3), `live` (SAT4+/PSAT#/SLT#), or `None`. Math already included SLT in its live-test regex; RW didn't (`_LIVE_SRC` was missing `SLT`) — real bug, was leaking full content for 59 questions that should've been hidden. Fixed, plus tombstone notes now say *why*: "Appears on a linear practice test. See SLT M/RW Uniques worksheet." when the trigger was SLT specifically, vs the generic "Removed. Appears on a practice test." otherwise.

### Practice test data now fully live
`load_test_data()` (generate.py:2115) used to read SAT4-11 module assignments from a static `practice-test-questions.json` and only fetch PSAT live. That JSON was stale — SAT11 had placeholder IDs (`a11rw101` etc.) instead of real content, SAT9/SAT10 each had 1-2 swapped questions. Rewrote to build both SAT and PSAT module lists from a single live spreadsheet fetch; the file is no longer read at all. Regenerated all 10 practice tests.

### Wide answer choices → 2-column grid
`_answers_need_grid()` (generate.py:240) flags an answer set as wide (table, svg, or a non-`math-img` raster image) so the `.answers-list` renders A/B in one column, C/D in the other instead of one tall stack. Caught 31 questions total across math (28) after the initial table/svg-only pass missed 3 that use full raster diagram images instead.

### Smaller fixes this session
- Universal CSS reset (`* { margin:0; padding:0 }`) was stripping list-item indentation, hiding bullet markers on all 192 Rhetorical Synthesis prompts (their notes are `<ul><li>`) — added back `ul/ol/li` padding.
- Chart SVGs with long rotated axis labels (e.g. `d5b9ed0d`) were getting bottom-clipped because `_move_svg_legend_right`'s height estimate used a flat buffer. Added a `_DOM_FIXUPS` step that expands each chart's viewBox to its actual rendered `getBBox()` after layout — self-corrects regardless of label length.
- `generate.py images --ids <id>` silently produced nothing for IDs with no local JSON data (e.g. `90095507`) — worksheets already had an openpathtutoring.com image-fallback path but the standalone `images` command never used it. Fixed, with the same "no SVG data / fallback available" reporting pattern used elsewhere.
- RW filename comma handling ended up asymmetric by explicit user instruction, not a general rule: **"Form, Structure, and Sense"** keeps its comma in filenames; **"Text, Structure, and Purpose"** does not, despite both having commas in the actual skill-name data (a data entry mistake the user is intentionally not fixing, to avoid breaking references in other spreadsheets). See the `file_skill` special-case in `run_rw_worksheets`'s `process_fn`.

### Image fallback recovery (cross-repo incident, not a generate.py change)
User accidentally deleted 542 fallback images in a separate repo (`~/code/openpath`, commit `836996b`, the one serving `openpathtutoring.com`). `question-images/` in *this* repo is gitignored so had no history to restore from; recovered via `git show 836996b^:<path>` in the openpath repo instead. Cross-referenced the 542 against the JSON banks (0 present — all are genuinely fallback-only by definition) and the master spreadsheet (only 53 are still-active questions; the other 489 are legacy placeholder-style IDs, e.g. `a07ma101`, no longer in the sheet). Restored all 542 locally into this repo's `question-images/`, but only committed+pushed the 53 active ones to openpath (commit `31370fa` on `origin/main`). Confirmed prod hadn't auto-deployed the fix — pushing to GitHub doesn't trigger deployment there; whoever/whatever deploys openpath still needs to pull `31370fa`.

**Artifacts (referenced, not restated):**
- `generate.py` — all changes above; uncommitted
- openpath repo, commit `31370fa` (pushed to `origin/main`) — restores the 53 active fallback images
- openpath repo, commit `836996b` — the original accidental deletion (still needs a prod pull/deploy)

**Open questions / blockers:**
- openpath prod still needs `31370fa` deployed — not automatic on push.
- The other 489 legacy fallback IDs were deliberately left deleted (not in spreadsheet); flag if that assumption is ever wrong.

**Next step:** Deploy `31370fa` to openpath prod (pull + restart/whatever the deploy process is there). No pending generate.py work.

## 2026-08-04 — generate.py: image consolidation, table fix, output structure

**State:** in-progress — all changes made to `generate.py`, not yet committed.

**What I did & why:**

### Image generator consolidation
Removed `IMAGE_CSS` (~250 lines) and `build_image_html` entirely. `render_one` now calls `build_worksheet_html` directly with a single question and screenshots `.question-block`. Goal: one CSS source of truth (`_worksheet_css()`) for worksheets, answer keys, and images. The header/number are hidden and 24px padding added in `render_one` via `page.add_style_tag` after `set_content`, so worksheet rendering is unaffected.

### Table width bug fix
A prior change added `width: 100% !important` to the `table` CSS rule to fix squished columns on questions like `1142af44`, `78391fcc`, `0366d965`, `1353b86e`. Root cause: those questions have `style="width:100px;"` inline — a pixel value that was beating the original `width: 100%`. `94237701` and similar looked fine because their inline width was `style="width: 25%;"` — a percentage that gave enough room visually. `!important` was too blunt: it also overrode legitimate partial-width tables. Fixed by reverting CSS back to `width: 100%` and instead stripping narrow pixel widths (`< 400px`) per-element in `_DOM_FIXUPS` JS before layout, leaving percentage widths alone.

### Practice test output structure
`_practice_test_worker` now creates `out_dir / test_key` (e.g. `practice-tests/SAT4/`) per test and saves both module PDFs and the combined PDF there instead of flat in `out_dir`.

### Second save location for concept worksheets
Added `import shutil` and three path constants (`_CONCEPTS_ROOT`, `_CONCEPTS_MATH`, `_CONCEPTS_RW`). After rendering each math/RW worksheet, copies it to `_CONCEPTS_MATH/{skill}/` or `_CONCEPTS_RW/{skill}/`. After rendering each answer key, copies to `_CONCEPTS_MATH/Answers/` or `_CONCEPTS_RW/Answers/`. Directories are created on demand. `--keys-only` flag is respected.

**Artifacts:**
- `generate.py` — all changes; not yet committed

**Open questions / blockers:**
- None; changes are complete and syntax-checked, but untested against live data.

**Next step:** Commit the accumulated changes (consider splitting into per-feature commits per CLAUDE.md convention), then run a test generation to verify image output and the second-location copies.
