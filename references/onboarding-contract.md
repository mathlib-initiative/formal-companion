# Onboarding contract — what a companion must expose, and what it must provide

Stage 0 of an audit. `cf onboard <repo>` reads the repository — and **never
writes to it** — detects the adapter, discovers the sources it names,
fingerprints the PDF bytes, infers the page offset, the item kinds, the errata
page and the anchor edition, prepares each source, writes
`companions/<name>/{adapter.yaml, conventions.md, statements.jsonl,
state.json}` into the **workspace**, and prints every inference with its
confidence and the file it came from.

There are no source flags. If you find yourself typing an offset or an errata
URL, discovery failed and said so; fix the signal or use the named override,
but do not fill it in by hand and call it onboarded.

**Contents** — §0 what discovery reads (the seven repo signals, where a PDF may be
opened from) · §1 a statement-table adapter, per convention · §2 an edition discovery
can resolve · §3 errata policy · §4 anchor convention · §5 house conventions ·
§6 sorry policy · §7 write policy and owner · §8 the dry run and what `state.json`
records · §9 two worked examples.

**A companion tree is read as DATA.** It is not built, not written to, and it
never gets to name a program: every `git` call against it goes through
`cflib.gitsafe`, which pins `core.fsmonitor`, `core.hooksPath`, `diff.external`
and the rest of the config keys git would otherwise execute on the repository's
behalf. (`git check-ignore` refreshes the index, and an index refresh runs
`core.fsmonitor` — so asking which files a tree ignores used to be an execve of
that tree's choosing.) Three more boundaries follow from the same rule:

* a **PDF path** the repo names is opened only if it resolves inside the repo,
  the workspace's `sources/` store, or a `library_paths` root the user
  configured; anything else is reported and left shut (§0.2);
* an **errata URL** the repo supplies is not fetched without an explicit
  opt-in (§3), because the page it returns is handed to workers as
  authoritative text;
* a PDF found in a `library_paths` convenience directory is adopted only when
  its filename is a registry alias or shares two title tokens — a directory the
  user keeps books in is not a claim about which book this is.

Onboarding is complete when `statements.jsonl` validates against the row schema
(`references/findings-schema-v2.md` §1) **and** `conventions.md` exists and has
been confirmed by someone who can speak for the repo. An unconfirmed convention
list is the single largest contributor to the ≈40% first-pass false-positive
rate (`references/method.md` §4): every false positive measured was a house
convention called a content bug.

§0 is what a repo must **expose** for discovery to work. §1–§7 are the seven
obligations — the contract proper, without which nothing downstream is
well-defined.

---

## 0. What discovery reads

### 0.1 The seven signals

All seven run for **every** adapter kind; the kind only sets the order and the
per-signal confidence, because a repo often carries a signal that "belongs" to
another convention. Each signal produces zero or more `SourceClaim`s, each
carrying the file it came from, and claims are merged into candidate sources by
full sha, by a resolved sha prefix, by a registry hit, or (last resort) by a
normalised title plus a compatible edition token.

| id | signal | file it reads | what it yields | conf |
|---|---|---|---|---|
| **S1** | project metadata | `formalization.yaml` → `sources:` | title, authors, ISBN and URLs parsed out of the free-text `id`, licence from `license` | 0.8 |
| **S2** | formalization ledger | `source-formalization-ledger.json` → `source: {kind, path, revision, sha256}` | `sha256` (authoritative), a PDF path hint, edition label from `revision` | 0.95 |
| **S3** | official-source record | `pdf-overlay/official-source.json` | `sha256`, download and book-page URLs, byte count | 0.95 |
| **S4** | graph metadata | `graph.json` → `metadata.sources[]` | title, PDF hint, an offset hint parsed from a `page_offset_note`, a scope hint | 0.7 |
| **S5** | prose | `CLAUDE.md`, `AGENTS.md`, `README.md`, `docs/**/*.md` | truncated sha **prefixes**, full shas, URLs, quoted `*.pdf` paths, offset sentences, the anchor-edition sentence | 0.5 |
| **S6** | filesystem | `sources/*.pdf`, `.lake/official-pdf/*.pdf`, `pdf-overlay/vendor/**/*.pdf`, other `*.pdf` outside `.lake/packages/` | PDF paths, and a real `sha256` once fingerprinted | 0.6 |
| **S7** | citation links | `README.md` head, `lakefile.toml`, `formalization.yaml` | book-page URL candidates, which is where errata usually live | 0.4 |

So, concretely: **a repo helps discovery most by naming its source in a
`sources:` list or a ledger `source:` block, saying in one sentence which
edition its page anchors follow, and citing the author's book page.** Those
three cost nothing and remove every flag from onboarding.

The **adapter is asked as well**, not only the seven signals: `cf onboard` merges
`Adapter.discover_sources(repo)` into them. A kind may only *add* a claim there —
a claim repeating a signal cflib already read is dropped in favour of cflib's,
because every later stage's item table comes from the source's own text and not
from the companion's reading of it — and the addition that matters is a
convention that **is its own manuscript**. Such a claim names the FILES instead
of describing a book: absolute paths in reading order, which onboarding
concatenates in exactly that order, with no separator, and fingerprints. That
digest is the `source_sha` every row carries, so an edit to any one file moves it.
The claim also says whether those bytes have printed pages (`sources/locator.py`
decides; a `.tex` manuscript has none), and `blueprint_tex` is the kind that uses
it — see §1.

**A source nothing can discover is named on the way in, not typed in.** Some
companions formalise a book that is not on this machine and that the repo never
names: no `sources:` list, no sha, no PDF, no book-page link. `cf onboard <repo>
--source <file>` (a PDF or a manuscript that is already text; repeatable) gives
the LOCATION and nothing else — the sha, the page offset, the item kinds, the
numbering depth and the completeness are still read off the file by `prepare`,
exactly as `cf sources add` reads them, and the source is registered
`confirmed: false` with `pdf_found_in: manual`. That is the same override
`cf sources add <source> --for <companion>` is, moved to the one command that
needs it *before* a companion exists — which is why it, and not `cf sources add`,
is what the "no source could be discovered" refusal names.

Two things follow from naming a location rather than a source:

- **One sha is one source, whichever door it came in by.** Naming a file whose
  bytes a repo signal also found does not add a second source: the fingerprint
  dedupe folds the two claims together, the repo's own name for the work survives
  (a declared title outranks a filename stem) and the path given is kept as
  evidence under `discovered.from`. Nothing about that is settled by a flag,
  because two ids on one sha is a state `workspace.yaml`'s own loader refuses.
- **A re-onboard does not have to repeat the flag.** A source with
  `pdf_found_in: manual` is invisible to all seven signals by construction, so
  `cf onboard` re-claims the ones this companion already lists, from the path the
  workspace recorded, and re-reads the facts off the file as on the first run.
  Only `manual` rows are re-claimed — everything else a signal finds again, and a
  stale workspace row must never outrank what the repo says today. If the file has
  moved, the run says so and names the flag again.

Two rules keep the signals honest:

- **A truncated sha is a candidate, never an identity.** `64498329…` in a
  `CLAUDE.md` is stored as a `sha256_prefix` and resolves only through a
  registry alias or a real fingerprint. A prefix that resolves to nothing is
  *reported* — it appears in the discovery result and in `cf status` — never
  adopted.
- **A contradicting sha is never adopted.** A file in the right place with the
  right name that fingerprints to another edition becomes a warning
  (`edition_mismatch: <path> is <sha8>, expected <sha8>`), because that is
  exactly how a batch of anchor findings once came to be judged against a draft.

### 0.2 Where the bytes are looked for

A repo usually does not commit the PDF. `locate_pdf` walks a strict order and
records which step won in `sources.<id>.discovered.pdf_found_in`:

| rank | where | recorded as |
|---|---|---|
| 0 | every path the repo names, resolved against the repo root and `~`-expanded | `repo_declared` |
| 1 | the repo's own caches: the same basename elsewhere in the repo, `.lake/official-pdf/`, `sources/`, `pdf-overlay/vendor/` | `repo_cache` |
| 2 | the workspace's own store, `<workspace>/sources/` | `workspace_store` |
| 3 | `workspace.yaml library_paths` — glob patterns, `~`-expanded, depth-capped, `*.pdf` only, joined by the derived roots: this workspace's `sources/` store, and `<p>/**/sources` + `<p>/**/.lake/official-pdf` for the parent `<p>` of every onboarded repo (including the one being onboarded, so run 1 and run 2 search the same set), never the bare `<p>`. Only `~/Downloads` is shipped: the list is also the allowlist for rank 0, so a home subtree in it hands that subtree to every audited repo. `cf init` and `cf doctor` print the effective set | `library_path` |
| 4 | the registry's `download_url`, **only** with `--download`, a workspace policy that allows it, and a licence note saying the author publishes it | `download` |

Within a rank: sha match first, then a registry `aliases.filenames` hit, then
≥2 tokens of basename overlap with the title/edition slug, then the PDF's
`/Title` metadata against a registry alias.

The basename match sits at rank 1 and not rank 0 on purpose. One target repo's
ledger declares `sources/gsm-126-tao5-measure-book.pdf`, that path does not
exist, and the bytes are actually in `.lake/official-pdf/`. Calling those bytes
"declared" would hide a stale declaration — the one fact a reader of
`pdf_found_in` most needs.

### 0.3 Which edition the repo anchors to

A repo may bundle one edition and anchor to another. Three rules, in order, and
the mechanical one decides:

1. **Unopposed** — exactly one candidate: it is the anchor, confidence 1.0.
2. **Anchor agreement** — for each candidate edition, score the companion's
   extracted `page_anchor` rows against that edition's `items.json`:
   `agreement = ok / (ok + mismatched)`. The winner is accepted only if it
   reaches 0.80 **and** beats the runner-up by 0.05. The confidence is the
   winning agreement.
3. **Prose corroborates only.** A sentence like "book-page anchors follow the
   PUBLISHED pagination" adds 0.05 when it agrees with rule 2; when it
   *disagrees*, the whole decision is refused, quoting both the sentence and
   the table. Prose alone, with no anchors to score, sets the anchor at
   confidence 0.6 and leaves the companion `confirmed: false`.

Measured on `tao-measure-lean` at commit `250b856` (2026-09-21): published
edition 0.91 (271 ok / 27 mismatched) against the draft's 0.68 (202 / 95) —
a margin far above 0.05, because both editions share `offset: 16` and differ
only by the one-page shift of ~65 items. Final `anchor_confidence` 0.96 after
the prose corroboration. The repo bundles the draft, so the draft is recorded
in `bundled_sources` with `role: reference`: prepared so the page map can be
checked, audited by nobody.

Below the thresholds `cf onboard` refuses and prints the table, with the fix:
`cf onboard <repo> --anchor-source <id>`.

### 0.4 What is inferred, and where to confirm it

| inferred | how | where to check it | how to override |
|---|---|---|---|
| page offset | mode of running-head votes over every page; `confidence = agreeing / voting`. **Below 0.9 it refuses.** An offset the repo's prose declares ("PDF page = book page + 16") is a CROSS-CHECK, never the answer: agreement raises the confidence floor and is recorded as `meta.json.offset_declared`, a contradiction is refused with both numbers quoted | `sources.<id>.offset_confidence`, `meta.json.offset_histogram`, `meta.json.offset_declared` | `cf sources set <id> offset=N` (`offset_method: manual`) |
| item kinds | capitalised words heading `<Kind> c.s.n` in the source's own text, count ≥ 3; structural words (`Chapter`, `Section`, `Figure`, …) reported separately and never used | `meta.json.kind_counts`, `kind_warnings` | `cf sources set <id> kinds=…` |
| errata URL | registry entry (1.0), else a book page the repo cites (0.8), else the download URL's parent page (0.5); the fetched HTML is then checked for the word "errata" and the confidence halved if absent. **Only a URL from a confirmed registry entry, or one a human typed, is fetched without asking** (§3) | `sources.<id>.errata_confidence`, `errata.json` | `cf sources set <id> errata_url=…` |
| scope | the chapters the companions actually tag: ≥5 items, or ≥20% of the book's items in that chapter | `sources.<id>.chapters`, `scope_method` | `cf sources set <id> chapters=1,2` (`scope_method: manual`) |
| adapter kind | per-adapter `detect(repo)`; highest wins, below 0.5 refuses | `companions.<name>.discovered.adapter_confidence` | `cf onboard --adapter <kind>` |
| anchor edition | §0.3 | `companions.<name>.discovered.anchor_confidence` | `cf onboard --anchor-source <id>` |
| which source | §0.1 — the seven signals plus whatever the adapter adds; nothing at all is a refusal | `sources.<id>.discovered.pdf_found_in`, `discovered.from` | `cf onboard --source <file>` (`pdf_found_in: manual`), or `cf sources add <source> --for <companion>` once the companion exists |

Two measured examples of the offset inference, both 265-page PDFs of the same
book: the published edition votes on 245 pages and 244 of them agree on 16
(0.9959); the 2011 draft votes on 249 and 248 agree (0.9960). That is what a
healthy inference looks like; 0.71 over 245 pages is what a refusal looks like.

A source discovery could not match to the shipped registry is written to the
**workspace overlay** (`<workspace>/registry.yaml`) with `confirmed: false`,
copied into `workspace.yaml` the same way, and listed by `cf status` under
*unconfirmed sources* with the command that settles it. See
`registry/README.md`.

---

## 1. A statement-table adapter

The adapter turns a repo at a pinned commit into `statements.jsonl`. Four
kinds, and every row carries `source_id` + `source_sha`, because an item is the
pair `(source_id, item)`.

Which source a row belongs to comes from `adapter.yaml`:

```yaml
sources:
  default: <source_id>          # with one source this is the whole story
  map:                          # with more, the FIRST matching rule wins
    - {source: <source_id>, files: ["Chapter2/**"]}
    - {source: <source_id>, tag_prefix: "AB:"}
```

A row that matches no rule is an error naming the file and the tag — never a
silent default, because a misfiled row joins against the wrong book.

### `docstring_tags`

Declarations whose docstring opens with a backticked book tag — `` `Exercise
1.1.3` ``, `` `Lemma 1.1.2(i)` ``, `` `In-text proof (book p. 23)` ``. Config:

```yaml
adapter: docstring_tags
glob: "TaoMeasure/Section_*.lean"        # files to scan
tag: {position: docstring_start, pattern: null,
      page_pattern: '(?:book\s+)?(?:pp?\.|pages?)\s*(\d+)'}
body_for_kinds: [def, abbrev, instance, structure, class]
```

The kind list is **not** in this file: it is the source's, inferred from the
source's own headings and stored in `sources.<id>.kinds`.

Detection: ≥20 `*.lean` files outside `.lake/` whose docstrings open with a
book tag, scored `0.6 + 0.35 × (tagged / documented)`, capped at 0.95. The
denominator is the sampled files that carry a docstring **at all** — a Lean file
with no docstring is not evidence against the convention. Measured on
`tao-measure-lean` (2026-09-16): 42 of 44 sampled documented files, while 110
of its 154 Lean files are generated blueprint stubs with no docstring. Over
`sampled` it would score 0.70 and look like a repo the skill barely recognises;
over `documented` it scores 0.93, which is what it is.

Three things the legacy extractors got wrong and the shipped one must not:

- **Prefer the item's own page over a cross-reference.** A docstring reading
  "previewed on book p. 20" gave one exercise the wrong anchor. Take the first
  page mention that is not inside a cross-reference clause, and record
  `anchor_source: own | crossref | none`.
- **End the item regex at `)` or newline.** A companion whose tag is a
  docstring-start label with a parenthetical leaked two rows of code into the
  item id.
- **Give anonymous `example`s a synthetic id** `example@<file>:<line>` so they
  are attributable. An exercise realised anonymously was dispatched as
  `SINGLE-SOURCE` although a second repo had in fact formalised it.

One parser, one kind list per source. Three parsers in one repo disagreed on
its declaration count — 1,248 vs 1,414 vs 1,413, with `Strategy` recognised by
only one. The shipped parser handles `private|protected|noncomputable|nonrec`
and `@[...]` attribute lines, and tracks namespaces so `decl` is fully
qualified.

### `attribution_table`

A machine-readable item→declaration map plus snippets, joined by the adapter.
The three inputs have defaults and are named here only to override them:

```yaml
adapter: attribution_table
glob: "pdf-overlay/data/*.json"
target_refinement:     pdf-overlay/data/target-refinement.json      # entries[] -> formalization ids
declaration_snippets:  pdf-overlay/data/declaration-snippets.json   # decl -> {file, line, text}
geometry_glob:         "pdf-overlay/data/geometry-batch-*.json"     # item labels + page regions
ledger:                source-formalization-ledger.json
```

Detection: `pdf-overlay/data/*.json` **and** `pdf-overlay/official-source.json`
both present → 0.98.

Two limits to state in `state.json` rather than discover later:

- **Coverage is bounded by curation.** The table covers the items someone
  curated. Measured 2026-09-16 at commit `9afe70f`: the adapter emits **562
  rows over 178 distinct items**, while the overlay defines **390 items** and
  the refinement table has **211 entries** (`state.json` `geometry_items`,
  `curated_entries`, `uncurated_items: 212`). Uncurated items are invisible to
  the differential, not absent from the repo — state the denominator.
- **Unnameable `example`s are excluded by construction.** The adapter must also
  scan the files the table maps to each item and emit `example@<file>:<line>`
  rows; otherwise the anonymous-realisation blind spot returns.

### `blueprint_tex`

A leanblueprint manuscript that writes the item→declaration map down instead of
leaving it to be inferred: `\begin{theorem}\label{thm:x}` + `\lean{Foo.bar,
Foo.baz}` + `\uses{def:y}` + `\leanok`. Config:

```yaml
adapter: blueprint_tex
glob: "blueprint/**/*.tex"               # recursive: a chapter is blueprint/src/chapters/*.tex
blueprint: {locator: auto, environments: [theorem, lemma, definition, proposition, corollary]}
anchor: none
```

Detection: `blueprint/**/*.tex` — **recursively**, because the macros usually
live in `blueprint/src/chapters/`, not beside the master file — scored
`0.6 + 0.35 × (statement environments carrying \lean{} / all of them)`, capped
at 0.95, and 0 when nothing carries one. `\newtheorem` scaffolding that defines
`\lean` as a no-op therefore scores nothing, which is the right answer.

Four things to know before onboarding one:

- **The item is a LaTeX counter, not a dotted book number.** `blueprint.locator`
  is `auto` by default: `number` when the preamble's `\newtheorem` declarations
  and the master file's `\input` order together make the printed number
  recoverable (`Lemma 2.3`, which then goes through the strict item grammar like
  any other item), otherwise the `\label` is the item. `dry_run` reports which
  (`locator`), and the two are never mixed — half a table keyed on `Lemma 2.3`
  and half on `lem:foo` is two namespaces pretending to be one.
- **`anchor: none`.** A blueprint has no separate published edition, so there is
  no pagination to anchor to and no edition drift to check. Rows carry
  `anchor_source: none`, and the finding kinds that make sense are
  statement-versus-manuscript disagreement and a stale `\leanok` — not page
  anchors. The environment body IS the manuscript, so the quote gate has
  something to quote with no PDF.
- **`\uses{}` is counted, not carried.** Row schema v2 has nowhere to hold a
  dependency edge, so `dry_run` reports `uses_edges` rather than dropping them in
  silence; a schema that can hold them is a separate change.
- **The manuscript registers itself as the source.** A blueprint names no book
  and ships no PDF, so `discover_sources` claims the `.tex` tree: the files in
  `order_files` order (the master's `\input` order, then anything the master does
  not name, in path order), `sha256` over their **concatenated bytes** in that
  order with no separator, `pagination: none`, `kinds` = the `\newtheorem`
  display words the manuscript uses, and `discovered.locator` = the `number`/
  `label` choice above. `cf onboard` writes that concatenation once into
  `<workspace>/sources/<id>/manuscript.tex` — never into the companion repo — and
  the workspace row points at it, so `cf sources refresh <id>` re-prepares the
  same bytes and a later `cf onboard` re-derives them. The source is
  `confirmed: false` like any other discovery.
  Two consequences worth stating. The sha covers every chapter, so editing one
  moves the `source_sha` on every row — which is the point: a digest that moved
  only when the master moved would stamp rows quoting an edited chapter with
  bytes they were not read from. And the prepared `items.json` is **empty on
  purpose**: the item grammar numbers printed-book headings and a `.tex`
  manuscript has none, so the items are the tagged environments the adapter
  reads. The "0 numbered items in its text" and "no declared kind heads a
  numbered item" refusals therefore do not apply to a manuscript the companion
  wrote — there is no other document these bytes could be mistaken for, they are
  the ones this adapter's own glob just matched — and they still apply,
  unchanged, to a PDF or text an operator named with `--source`.

### `ledger_nodes` (deferred in v1)

A source-node → formalization ledger with prose locators. Its granularity is a
*source node*, not a book item (2,884 formalization nodes and 77 source nodes
in the audited export), so the adapter declares `granularity: node` and the
workspace must run an item-level curation pass (curator + independent verifier
per node) before extraction. **v1 ships the declaration but not the curation
workflow**: `cf onboard --adapter ledger_nodes` refuses with a pointer to this
paragraph *before* it writes anything. The kind is in `vocab.ADAPTER_KINDS`
(what an `adapter.yaml` may legally say) but not in
`vocab.IMPLEMENTED_ADAPTER_KINDS` (what `adapters.api.registry()` actually
provides, which a test pins). The same holds for `custom`.

Two extra state fields are mandatory for ledger repos, because the ledger is an
*export*: it goes stale the moment a fix names a new theorem, and `extract`
keyed on (repo commit, adapter sha) alone would silently reproduce the pre-fix
table.

```yaml
key_files: [source-formalization-ledger.json, pdf-overlay/data/target-refinement.json]
stale_since_commit: null   # set when the repo moves ahead of its exported ledger
```

### `custom: <script>`

Any executable emitting the row schema on stdout. The schema gate applies
unchanged; the rows still carry `source_id` and `source_sha`.

---

## 2. An edition discovery can resolve

The repo does not have to *commit* a PDF, and does not have to type an offset.
It has to make its edition **identifiable**. In descending order of usefulness:

1. a full `sha256` of the PDF in a ledger, an official-source record or prose;
2. a stable download URL or book page for the edition (registry alias
   material);
3. a filename that matches what the author publishes;
4. a title plus an edition label distinctive enough to tell siblings apart.

What the skill derives once it has the bytes: the page offset (printed page =
PDF page − offset), the page count, the kinds, `text_sha256`, and the printed↔
PDF direction. What it still needs from the repo is **which edition the anchors
follow** (§0.3) and whether an anchor is a printed page or a PDF page (§4).

Two editions of one book differ by a page: ~65 numbered items sit one printed
page earlier in the published edition than in the 2011 draft, nearly all in one
section (the docstring-tag companion's own `CLAUDE.md` says so). Both
satisfy `pdf_page = printed_page + 16`, so mixing them produces a page-anchor
finding for every one of those items — off by exactly one, never by sixteen —
and an adjudicator checking the other file refutes them all. `source_sha` is
stamped into every artifact and the gate refuses a mismatch; the sibling
relation and its shift live in the registry's `page_map_to`.

## 3. Errata policy

Discovery finds the URL (§0.4); the repo's job is to cite its book page, and
the audit's job is the disposition rule. The skill fetches the live page with
`curl` and keeps the **raw HTML** beside the rendered markdown, dated — never a
summarising fetch tool, which is what silently dropped the entry that decided a
high-severity finding (`tao-measure-lean/docs/HANDOFF.md` §4, `REVIEW_PATTERNS.md` P13). The
fetch metadata lands in `sources/<id>/errata.json`: URL, HTTP status, both
shas, byte count, entry count, `changed` against the previous fetch, and
`url_provenance` — **who chose the URL**.

Who chose it decides whether it is fetched at all. A URL from a **confirmed**
registry entry, or one a human typed with `cf sources set <id> errata_url=…`,
is fetched without asking. A URL harvested from the companion's own files — the
free-text `id:` field of `formalization.yaml`, a README link, the parent
directory of a download URL the repo names — is not: `cf onboard` prints it,
says where it came from, and stops. `--fetch-errata` (or
`policy.allow_errata_fetch: true`) allows it once, and what it fetches is
marked `repo_supplied: true`, which `cf run` refuses to transfer to a fleet
host. This is not pedantry about the network: the errata page is handed to
workers as text they must treat as authoritative, so a repository that chose it
would be writing into the audit's prompts.

**The four dispositions**, each evidenced in the audited campaign:

| the erratum … | disposition | evidence |
|---|---|---|
| **corrects** the printed statement | formalise the corrected statement; kind `SUSPECTED_ERRATA` until confirmed, then fix | the exercise whose labels the errata swapped, and the one whose sharp constant it corrects (both fixed in PR #8) |
| **observes** a divergence and points elsewhere (e.g. a blog post) | formalise the book as printed; record the argument in the docstring; resolution `argued_correct` | the definition/remark pair deliberately not acted on (`tao-measure-lean/docs/HANDOFF.md` §5): rewriting the clause would formalise the blog post and make the companion *less* faithful |
| **asks for a generalisation** nobody formalised | kind `COVERAGE`, out of scope for a fix | the exercise the errata generalises from elementary to Jordan-measurable sets; all three companions omit it — a *missing* theorem, not a wrong one |
| **deletes** an item or a part | do not formalise it; a declaration that states it is a finding | the errata list records one exercise part as wrong and deleted (`memory/measure-theory-faithfulness-audit.md`) |

An erratum is consulted **before** ruling either side wrong; both the worker and
the reviewer prompts say so.

## 4. Anchor convention

`anchor: docstring | ledger | none`.

- `docstring` — page anchors live in docstrings and are the adapter's
  `page_anchor`.
- `ledger` — anchors come from the ledger/overlay. **Helper ids may embed
  `:L<n>:` line numbers**, so a docstring edit must keep the line count or the
  whole pin cascade must be re-run.
- `none` — the companion carries item labels only; `anchor_check` skips it, a
  page-anchor finding against it is invalid, and anchor-agreement (§0.3) cannot
  decide its edition, so it needs a prose statement or `--anchor-source`.

Anchors are **printed** pages in the row schema. The conversion happens in one
place, from the source's `offset`.

## 5. House conventions — `conventions.md`

The list that decides `CONVENTION_ONLY` versus `CONTENT_DELTA`. It is injected
verbatim into every worker, reviewer, attack and adjudication prompt, so it is
load-bearing for the false-positive rate. Drafted by an onboarding agent from
the repo's `CLAUDE.md`, module headers and design rules; **confirmed by the
owner**. For a repo with no reachable owner, mark it
`confirmed_by: unconfirmed` — and treat every finding that rests on it as lower
confidence.

Required sections:

1. **Encoding conventions** — ambient space, value types (e.g. an
   extended-nonnegative codomain), indexed families vs finite sets, bundled vs
   unbundled hypotheses, subtype bundling, namespace.
2. **Total functions with junk values** — *the explicit list of names* (P9).
   This list is what the `attack:vac` prompt probes: which functions in this
   repo return a value where the object is undefined.
3. **Standing conventions** — *the explicit list* (P12): degenerate dimension,
   a standing `1 ≤ d`, which cases the repo's own docstrings exclude, where the
   companion stops. This list is what the `attack:hunt` prompt checks
   statements against; it found the cheapest high-severity defect in the
   audited campaign with no reference to the book at all.
4. **Scope** — which sources and which chapters the companion covers, and which
   edition its anchors follow. "Stops at §1.4" is a scope fact, not a defect,
   and every prompt must say so or the run reports the absence. The chapter
   list is derived (`sources.<id>.chapters`); this section is where a human
   explains it.
5. **Deliberate deviations** — documented narrowings, with their justification.
   Flag each one for the P11 check: *a documented restriction is not a
   justified one*.

## 6. Sorry policy

```yaml
sorry:
  exercise_sorry: permanent | none
  gap_marker: "sorry -- TODO(autoform)"
  classifier: scripts/update_status_table.py   # optional: the repo's own authoritative split
```

`sorry` rows are **classified**, never reported: `sorry_class ∈ exercise | gap
| none`. Where the repo owns a classifier, the adapter calls it — the
authoritative split for one audited repo is 510 exercise + 6 in-text = 516,
while `grep -c sorry` gives 523 because 7 hits are prose. A companion with
`exercise_sorry: permanent` must never have an exercise solved by the fix
stage; that is a merge invariant, checked before and after.

## 7. Write policy and owner

```yaml
write_policy: read_only | branch_only | branch_and_pr
owner: session:<id> | github:<login> | external | none
validators: ["python3 scripts/update_status_table.py --check"]
post_change_cascade: "<repin command>"     # ledger/viewer repos only
consent: { heavy_runs: required, recorded_in: run.yaml }
```

- **Peers default to `read_only`**, and v1 is findings-only, so nothing writes
  to a companion repo either way. In the audited campaign the third companion
  was read-only to the auditing session throughout, and every merge was one
  command on the owner's go.
- `branch_and_pr` unlocks the (post-v1) fix stage; the invariants gate runs
  first and opening or merging still waits for a human.
- Repos whose data files are pinned by sha need their **cascade command**
  recorded here, because a Lean change invalidates the ledger, the snippets,
  the overlay manifest, the validator constants and the viewer's binding at
  once.

---

## 8. The dry run, and what `state.json` records

`cf onboard <repo>` (or `--dry-run` to print and write nothing) reports, and
writes into `companions/<name>/state.json` (version 2):

- rows, distinct items, tagged vs untagged declarations, private/excluded
  declarations;
- `sorry` counts by class;
- anchors by provenance (`own` / `crossref` / `none`);
- item kinds seen, and the **rejected item strings with their reason** — never
  dropped silently;
- `repo`, `commit`, the adapter kind and its confidence, and for ledger repos
  `ledger_sha` plus whether it is stale;
- `sources: {<source_id>: {sha, rows, items, at}}` and `anchor_source` — a map,
  not one top-level sha, because a companion may be bound to several sources.

Measured on the two targets, 2026-09-21, with this revision of the skill:

| | `tao-measure-lean` @ `250b856` | `measure_theory` @ `9afe70f` |
|---|---|---|
| adapter | `docstring_tags` (0.93) | `attribution_table` (0.98) |
| files scanned (detection's `lean_files`: every `.lean` file outside `.lake/`. The dry run's `files` key is 112, the files the adapter itself parses — the glob minus `exclude:` minus what the repo git-ignores — and 33 of those carry a tag and yield rows) | 154 | — (table-driven) |
| declarations | 1,556 (1,132 tagged, 424 untagged, 130 private) | 531 |
| rows / items | 1,131 / 506 | 562 / 178 |
| anchors | 524 own, 1 crossref, 606 none | from the overlay |
| `sorry` | 516 (508 exercise, 8 gap) | 0 |
| rejected tags | 1 (`Corollary (Lemma 1.2.5 + Exercise 1.2.4)`, `no_number`) | 32 unnumbered labels |

The contract also calls for a **full declaration index** beside the table
(`declindex.jsonl`: every declaration in the glob, tagged or not), because the
schema gate validates `repo:decl` strings against *that* index and not against
`statements.jsonl` — workers legitimately name helpers, private lemmas and
anonymous examples that are not book items, and validating against the item
table alone would reject real findings. v1's shipped adapters count the
untagged declarations into `state.json` but do not yet write the index file; a
gate configured to demand it must be given one.

---

## 9. The two concrete examples

### A. `tao-measure-lean` — `docstring_tags`, two editions, one anchor

What discovery found with no flags: S1 `formalization.yaml#sources` (title,
authors, ISBN, the preprint URL, the licence note), S4
`graph.json#metadata.sources` (the bundled PDF path and an offset note), S5
`CLAUDE.md` (both sha prefixes, the "both satisfy PDF page = book page + 16"
sentence, the ~65-items note and the anchor sentence), S6 the committed
`sources/tao-measure-theory.pdf`, S7 the author's book page in `README.md`. Two
claims resolved to `tao-measure-2011-draft` (fingerprinted in the repo,
`repo_declared`) and `tao-measure-gsm126-published` (prefix-resolved through
the registry, bytes located in the other companion's `.lake/official-pdf/`).
Anchor decided mechanically for the published edition; the draft filed under
`bundled_sources`, `role: reference`.

```yaml
# companions/tao-measure-lean/adapter.yaml, as cf onboard wrote it
adapter: docstring_tags
granularity: item
sources: {default: tao-measure-gsm126-published, map: []}
glob: "**/*.lean"
exclude: [".lake/**/*.lean", "**/.lake/**/*.lean"]
anchor: docstring
sorry: {exercise_sorry: permanent, gap_marker: "sorry -- TODO"}
write_policy: read_only
owner: "external"
```

Narrow the `glob` and name the repo's own `sorry` classifier by hand afterwards
(`sorry.classifier`, a path inside that companion repo — §6), then re-run
`onboard --force` to re-check the parse: onboarding writes a working default,
not the last word.

Its `conventions.md` carries the junk-value list (its measures are total
functions on sets, never on proof terms), indexed families rather than finite
sets of sets, the ambient Euclidean space, permanent exercise stubs, and the
docstring-tag rule itself. Its review catalog (`REVIEW_PATTERNS.md`) is
mandatory reading before review, which is why `cf lint` (specified in
`references/review-patterns.md`, not implemented in v1) ships the same
detectors.

### B. `measure_theory` — `attribution_table`, one source, sha-confirmed

S2 gives the authoritative `sha256` and a declared path; S3 adds the download
URL, the book page and the byte count; S6 finds the bytes in
`.lake/official-pdf/` (so `pdf_found_in: repo_cache`, because the declared path
does not exist); S7 confirms the errata page from `README.md`. One claim, one
candidate, anchor by `single_candidate` at confidence 1.0.

```yaml
adapter: attribution_table
granularity: item
sources: {default: tao-measure-gsm126-published, map: []}
glob: "pdf-overlay/data/*.json"       # the three inputs keep their defaults
anchor: ledger                        # helper ids embed :L<n>: — keep docstring line counts
write_policy: read_only
owner: "external"
# added by hand: the export shas that must re-key an extraction, the repo's
# sorry policy, and the cascade a Lean change triggers
key_files: [source-formalization-ledger.json, pdf-overlay/data/target-refinement.json]
sorry: {exercise_sorry: none}
post_change_cascade: "<repin command> <root> <commit>"
```

Definition bodies are carried in `statement`/`body` on purpose, which is how a
proxy definition was spotted. Its `conventions.md` records subtype bundling,
the namespace, zero `sorry` by design, and that unnameable `example`s cannot be
attributed in its viewer.

A third companion of the same book carries no page anchors at all and stops
partway through the chapter — `anchor: none`, a scope line in `conventions.md`,
and an explicit `--anchor-source` because §0.3 rule 2 has nothing to score.
Nothing in the design precludes onboarding it; it simply contributes to the
differential wherever its source list overlaps the others.
