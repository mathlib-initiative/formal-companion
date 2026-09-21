# Adding an adapter: teaching the skill a new companion convention

An **adapter** is how one repository convention becomes rows of a statement
table. The three that ship read a docstring tag, an attribution table and a
leanblueprint `.tex` tree; a fourth convention is a new file under
`scripts/adapters/` and no change to the core. `CLAUDE.md` §6 is the contract;
this is the walkthrough, against a fixture you can generate in ten seconds.

## 0. Get a repo to work against

```sh
bash tests/e2e/synthetic.sh          # builds the booklet and two companions
```

`tests/e2e/make_notes_repos.py` writes `notes-a` and `notes-b`: real git repos
with a real `formalization.yaml`, real docstring tags and one declaration per
file. They are never built — `lake` is never run — because what an adapter
reads is the tag, the page and the statement. Copy one and rewrite its
convention into whatever shape you are adding; that is a faithful test case
because `cf onboard` discovers it exactly as it discovers a real companion.

## 1. Detection: the class scores itself

```python
@adapters.api.register
class MyConventionAdapter(adapters.api.Adapter):
    kind = "my_convention"

    @classmethod
    def detect(cls, repo) -> tuple[float, dict]:
        ...
        return score, {"reason": "…", "<measurement>": …}
```

`cflib.discovery.detect_scores` calls `detect` on every class in
`adapters.api.registry()`, `cf onboard` takes the highest, and anything below
`MIN_ADAPTER_CONFIDENCE` (0.5) is a refusal that names `--adapter`. So
detection is a loop over the adapters and your convention adds no line to the
core.

Two things that decide whether the score means anything:

- **Count a denominator that means something.** `docstring_tags` scores over
  files that *carry a docstring*, not over every `.lean` file in a tree that is
  mostly generated stubs, and it refuses to score a repo with fewer than
  `MIN_LEAN_FILES` (20) of them at all: three Lean files are not evidence of a
  convention. Satisfy a floor like that rather than lowering it.
- **Put the measurement in `evidence`.** `cf onboard` prints it, stores it in
  `state.json`, and writes the keys your adapter names into the generated
  `adapter.yaml`. That is how `tag.require_backticks` stopped being a
  hard-coded default and became a per-repo spelling the measurement found.

`explain(cls, repo)` is the other half: one "looked for / found" line per
signal, for a repo you did *not* claim. Those lines are the whole content of
the refusal a user sees when nothing matched, so name the override that would
settle each one.

## 2. Sources: what the repo says about the book

`discover_sources(repo) -> [SourceClaim]` adds to the seven repo signals in
`references/onboarding-contract.md` §0. Each claim carries the file it came
from. Never invent a `sha256`: a truncated sha in prose is a `sha256_prefix`,
which is a candidate, not an identity.

A convention whose manuscript **is** its own source says so with a claim whose
`files` name it, in reading order — `blueprint_tex` does this for a `.tex`
tree, and onboarding concatenates those files in that order and fingerprints
the result.

## 3. Rows

`dry_run(repo)` reports what extraction would produce; `extract(repo, *,
sources={id: sha})` produces it. Rows follow `ROW_REQUIRED_FIELDS`, which
includes `source_id` and `source_sha`; declarations are fully qualified (track
namespaces), and an anonymous example gets a synthetic
`example@<file>:<line>` id so it can still be attributed.

`source_for(...)` decides which source a row belongs to, from `adapter.yaml`'s
`sources: {default, map}`. A row that matches no rule is an error naming the
file and the tag, **never** a silent default: attributing a row to the wrong
edition is the defect class this audit exists to find.

`key_inputs()` must list every input whose change changes the result — each
export sha the adapter reads, plus the shas of the sources it is bound to.
`cf extract` is a no-op while that key is unchanged, so an input you forget is
a stale statement table nobody notices.

## 4. Registering it

1. `@adapters.api.register` on the class.
2. Add the module to the import inside `adapters.api.registry()` — that import
   is what makes the kind exist before anything asks for it.
3. Add the kind to `vocab.IMPLEMENTED_ADAPTER_KINDS` **only when it really
   works**. `ADAPTER_KINDS` is the wider vocabulary an `adapter.yaml` may
   legally name, and a test pins the narrower set against what `registry()`
   provides. A kind that is named but unimplemented used to win a detection and
   then refuse the onboarding.
4. Add a row to `cfcmd.onboard.DEFAULTS` (glob, anchor, granularity) and a
   default block in `templates/adapter.yaml.example` for anything it
   configures.
5. Add the kind to `references/vocabulary.md` and describe the convention in
   `references/onboarding-contract.md` §1, in the shape of the three that are
   there. The refusal points users at that file, so a kind it does not describe
   is a kind nobody can adopt.

## 5. Checks

```sh
python3 scripts/cf.py onboard <your fixture> --dry-run --no-errata
python3 -m unittest discover -s tests
```

The dry run is the real test: it prints the detection score, the sources
discovered, the inferences and the rows, and it writes nothing. Then a unit
test with a fixture repo — no network, no PDF, no companion checkout.

## A note on new *books*, which is a different job

An adapter is a repository convention. A new **source** needs nothing from
`scripts/adapters/`: discovery and inference read it from the text. The one
per-source artifact anybody writes by hand is the stage table that
`cf detect-proxies --lean` needs —
`scripts/detect/mathlib_stages/<source_id>.json`, saying which section of the
work first develops a concept. Without one the cheap tier still runs and the
body-proxy scan reports that it was **skipped and why**, rather than reporting
no proxies. `cf detect-proxies <name> --source <id> --init-stage-table` writes
a skeleton for a human to fill in, and the skeleton deliberately counts as no
table until its `_unedited` key is deleted:
`tests/e2e/stages.anonymous-elementary-sets.json` is a filled-in one, for the
fixture.
