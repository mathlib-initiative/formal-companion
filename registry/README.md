# The source registry

`registry/sources.yaml` is what the skill **remembers about an edition** so that
nobody has to type it again. It is the reason `cf onboard <repo>` needs no
flags: a repo names a book, discovery fingerprints the PDF, and the sha256 of
those bytes is looked up here — title, authors, edition label, page offset, item
kinds, errata URL, licence, and how this edition's printed pages map onto its
siblings.

Two files, one schema, different authority:

| file | authority | committed | written by |
|---|---|---|---|
| `<skill>/registry/sources.yaml` | curated, shipped with the skill | yes, in this repo | humans. **No command writes it** |
| `<workspace>/registry.yaml` | that workspace's overlay | yes, in the workspace repo | `cf onboard`, `cf sources add` — always with `confirmed: false` |

Lookup is **overlay first, then the skill**, merged field by field: an overlay
entry with the same `source_id` shadows the shipped one, the overlay's non-null
keys winning (`cflib/registry.py` `Registry.load`). A command never writes
`confirmed: true` into an overlay. A human promotes an entry by copying it into
this file with `confirmed: true` and deleting the overlay row.

## Metadata only — never a line of the book

Shas, URLs, page maps, offsets and licence notes are *facts about* an edition.
The prose is copyrighted and lives, gitignored, in a workspace's
`sources/<source_id>/`. That boundary is what makes it safe to commit this file
to a repo that may be shared, and it is a hard rule, not a convention:

- no book text, no quotations, no extracted page snippets, no items list;
- `download_url` is recorded **only** where the author publishes the file on his
  own site, and only alongside a `licence.note` saying so;
- `licence.redistribute` is `false` for every entry, and `Registry.validate()`
  reports an entry that says otherwise as a problem. Nothing this skill fetches
  is ever re-served, committed or copied to a host that did not already have it.

`POLICY.md` is the whole rule, including what you assert by adding an entry and
what happens if a rights holder objects. The mechanical half is a command:

```sh
cf sources validate-registry            # the shipped file
cf sources validate-registry --overlay  # and this workspace's
```

It checks the structure `Registry.validate()` knows about — required fields,
the sha shape, the licence enum, page-map symmetry, `same_work` naming its
partner back — **and** the part the dataclass cannot see, because
`RegistryEntry.from_dict` silently drops keys it does not know: an unknown
field, and any string outside the four free-text fields (`notes`,
`licence.note`, `page_map_to.*.note`, `page_map_to.*.confirmed_by`) that runs
past a word cap or into a second sentence of prose. A passage of the book cannot
arrive here by accident in a field nobody reviewed. An edition statement after a
full stop is not a second sentence — "A Course in Functional Analysis. Second
edition" and an author's `John B. Conway` pass, because that is what these fields
are for. CI runs the same command on every push.

A download happens only when all three hold: `--download` was passed to
`cf onboard`, the workspace's `policy.allow_download` is not false, and the
entry's `licence.fetch` is `author_site` or `open` (`RegistryEntry.may_fetch`).
`fetch: none` refuses even with the flag.

## What an entry holds

```yaml
version: 1
sources:
  <source_id>:                  # the slug rule is docs/design/archive/multi-source-model.md S3
    title: …                    # required
    authors: [ … ]
    edition: …                  # required: the label that distinguishes it from its siblings
    isbn: … | null
    sha256: <64 hex>            # identity: the sha of the PDF BYTES
    bytes: <int>
    pages: <int>
    download_url: … | null      # only when the author publishes it himself
    book_page_url: …            # the page that describes the book
    errata_url: …               # usually the same page; this is what `cf sources refresh` fetches
    licence: {note: …, fetch: none|author_site|open, redistribute: false}
    licence_class: closed|open_attribution|open_share_alike|public_domain   # default closed
    offset: <int>               # printed page = PDF page - offset
    offset_confidence: <0..1>   # how the number was arrived at, and how sure
    offset_method: running_head_mode | declared | manual | none
    kinds: [Definition, Lemma, … ]
    same_work: [<source_id>, …] # other editions of the same book
    page_map_to:                # how printed pages map onto a sibling
      <other_source_id>:
        default_shift: <int>    # other_page = this_page + default_shift for most items
        exception_shift: <int>  # …and + exception_shift for the items in `exceptions`
        exceptions: [<item>, …] # empty means "see the note"
        note: …                 # REQUIRED when `exceptions` is empty
        confirmed_by: …         # who established it, and where they wrote it down
    aliases:                    # the extra keys discovery matches on
      sha256_prefixes: [ … ]    # truncated shas quoted in a repo's prose
      urls: [ … ]
      filenames: [ … ]
      titles: [ … ]
    confirmed: true|false
    edition_status: current|draft|superseded   # default current
    notes: …
```

`edition_status` is what `cf ingest` reads before it starts a pipeline: a
`draft` or `superseded` edition is refused unless `--override-edition` is
passed, and the override is recorded in the stage receipt
(`docs/pipeline.md`).

`licence_class` is what an *exported* record may quote of the edition
(`POLICY.md` §4.1, `cflib.vocab.LICENCE_CLASSES`). It is optional and
`closed` when absent, so every entry written before it existed is `closed` and
still valid; `cf sources validate-registry` refuses any other value. A command
writing an overlay row writes the key only when it is not `closed`: the overlay
is merged over the shipped entry key by key, and a `closed` written everywhere
would close a source the shipped registry calls open.

A source need not be a paginated PDF. A manuscript that is already text (an
arXiv `.tex`, notes in Markdown, reStructuredText or HTML, a `pdftotext` dump
kept as a file — `.txt .md .markdown .tex .html .htm .rst`, exactly the set
`scripts/sources/locator.py` accepts) is added the same way — `cf sources add
<source> --for <companion>` — and if it carries no page breaks it is
**unpaginated**: `pages: 0`, `offset: 0`, `offset_method: none`, and no
`page_map_to`, because it has no printed page for a map to shift. Its items are located by heading path and line instead, recorded
as `locator: section` in the workspace's `sources/<id>/items.json`; the
workspace row (never this file) carries `source_format` and `pagination`. Every
page-dependent feature refuses on such a source by name rather than compute a
page nothing supports — see `scripts/sources/locator.py`.

`page_map_to` is **not symmetric by construction** — both directions are written
out, and `Registry.validate()` checks that they agree: `a→b.default_shift ==
-(b→a.default_shift)`, likewise `exception_shift`, plus a `note` whenever
`exceptions` is empty, plus `same_work` naming its partner back.

## Web-source families

A web page is not an edition with a PDF: English Wikipedia alone has millions
of revisions, and a registry row per revision would be a corpus, not metadata.
So the registry's second map, `families:`, holds one row per **site** — what
every revision of every page of it shares — and **no per-revision rows**:

```yaml
families:
  enwiki:                       # 2-24 lowercase letters and digits, no '-'
    title: English Wikipedia    # required
    site: https://en.wikipedia.org
    container_title: Wikipedia  # the CSL container-title of every record
    licence_class: open_share_alike
    licence: {spdx: CC-BY-SA-4.0, note: …, redistribute: false}
    attribution: '"{title}", Wikipedia, revision {revision}, {url}, licensed {licence}'
    revision_url: "https://en.wikipedia.org/w/index.php?curid={pageid}&oldid={revision}"
    confirmed: true
    notes: …
```

A web source's id is `<family>-<pageid>-<revision>` — `enwiki-28767-1371234567`
(`cflib.registry.web_source_id`, `parse_web_source_id`) — keyed by page id and
MediaWiki revision because titles carry `/` and move. Its shas, its title and
its assessment live in a sourceset manifest beside the workspace's sources (*not
implemented*), never here. `Registry.family_of(source_id)` finds the family,
`SourceFamily.attribution_for(title=…, pageid=…, revision=…)` fills the
attribution template, and `SourceFamily.revision_link` the permalink.

What `cf sources validate-registry` checks on a family: no key outside the list
above (a `sha256`, `pageid` or `revision` is refused — a family holds nothing
about one revision), `licence_class` in the enum, `licence.redistribute` false,
an open-attribution or share-alike family naming its licence as `licence.spdx`,
an `https://` site and permalink, and two templates that name only their own
fields — the attribution `{title}`, `{pageid}`, `{revision}`, `{url}` and
`{licence}`, of which it must name `{title}`, `{url}` and `{licence}` (what CC BY
and CC BY-SA attribution is), and the permalink `{pageid}` and `{revision}`, of
which it must name `{revision}`. The free-text caps apply as for an entry, with
`attribution` capped at 40 words. A registry entry whose id is a web source id
of a known family is refused: it is a sourceset row, not an edition. **Nothing
here fetches**: the fetcher a family will use is *not implemented* (`CLAUDE.md`
§3 names it as a network exception).

## What ships today

Two editions of one book, both fingerprinted on the machine this skill was
written on:

| `source_id` | sha256 (first 8) | bytes | offset | measured |
|---|---|---|---|---|
| `tao-measure-gsm126-published` | `64498329` | 1,338,765 | 16 | mode 16 on 244 of the 245 pages carrying a running-head number → 0.9959 |
| `tao-measure-2011-draft` | `62d95658` | 1,218,984 | 16 | mode 16 on 248 of 249 → 0.9960 |

Beside them ships one web-source family, `enwiki` (English Wikipedia,
`open_share_alike`), and no web source at all.

The two editions are `same_work`, and their page map is the whole reason this file exists:
roughly 65 numbered items, nearly all in one section, print **one printed page
earlier** in the published book than in the draft. Both editions satisfy
`pdf_page = printed_page + 16`, so a companion anchored to the wrong one is off
by exactly one page, never by sixteen — which is precisely the error that is
invisible to a spot check and produced a wave of page-anchor findings that all
had to be withdrawn.

## Adding an edition

This file is the contribution surface: adding a book is how somebody else's
work reaches this tool, and it is the one file written by hand.

1. Onboard the repo that names it. Discovery derives a `source_id`, writes what
   it learned to the workspace overlay with `confirmed: false`, and `cf status`
   lists it under **unconfirmed sources** with the command that fixes it.
2. Check the entry: `cf sources show <id>`. Is the edition label the one a
   reader would use? Is the offset's confidence above 0.9 and its method
   `running_head_mode` rather than `manual`? Does the errata URL actually carry
   errata? Then `cf sources set <id> confirmed=true`.
3. `cf sources promote <id>` writes the row out as
   `registry-patch.<id>.yaml` — a metadata-only, `confirmed: true` entry,
   checked before it is written. Paste its `sources:` entry into this file in a
   pull request, keeping the map sorted. **No command writes this file from a
   workspace**, deliberately: an entry here is a fact for every future
   workspace, so a human reviews the diff. Once it ships, delete the row from
   the overlay.
4. Where it is another edition of something already listed, write **both**
   directions of `page_map_to` and say in `confirmed_by` who established the
   shift and where. Add `aliases` for the filenames and URLs it travels under.
5. Never add a `sha256` you did not compute from the bytes yourself. A
   truncated sha quoted in a repo's prose belongs in `aliases.sha256_prefixes`,
   which is a matching hint; it is not an identity.
6. Run `cf sources validate-registry` in the checkout you edited before opening
   the PR. CI runs it too, but the error is easier to read locally.

## Calibration facts

`registry/calibration.yaml` is the second file here, and a smaller one: the
companion revisions a milestone of `docs/proposals/pdf-to-companion.md` replays
against or compares with, each with the date, the pull requests around it and
the reasoning that found it. The first entry is the tao-measure-lean revision
before the camera-ready fix stack (`d8cdfd5`, the first parent of the stack's
first merge), which sections 5.1, 5.4 and 5.5 of the proposal need. The same
rule as the source registry applies: a commit, a date and a sentence of
reasoning are metadata; a line of Lean or of a book is not, and no owner is
named. No command writes the file; `tests/test_calibration.py` checks its shape.
