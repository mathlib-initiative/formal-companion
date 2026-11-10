# formal-companion

[![tests](https://github.com/mathlib-initiative/formal-companion/actions/workflows/tests.yml/badge.svg)](https://github.com/mathlib-initiative/formal-companion/actions/workflows/tests.yml)
[![license: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)

formal-companion audits Lean 4 book companions against the sources they formalize,
generates PDF overlays that link each item on a page to its declarations, locates
where a source's passages are formalized in Lean libraries, and writes the informal
text of a Lean theorem and its dependencies from the Lean itself.

A *book companion* is a Lean repository whose declarations claim to formalize the
numbered items of a textbook, lecture notes or a preprint. A proof of the wrong
statement still compiles, so an extra hypothesis, a missing quantifier or a vacuous
theorem passes `lake build`. An audit compares each statement with the source:
deterministic detectors settle what they can, language-model agents read the rest, and
an adversarial pass challenges what they report.

It is a standard-library Python CLI, `cf`, and also a Claude Code skill,
`companion-faithfulness`.

## Try it

The demo needs Python 3.11+, git and poppler's `pdftotext`, and no network, `lake` or
language model.

```sh
git clone https://github.com/mathlib-initiative/formal-companion &&
  cd formal-companion &&
  bash tests/e2e/synthetic.sh
```

It writes a public-domain booklet and two Lean companions of it with eight planted
defects, runs the model-free detectors and scores them against an answer key. They find
the four defects of the kinds they can see. The other four take a reader, which is what
the model tier is for.

## The four tools

| Tool | Question it answers | Status |
|---|---|---|
| [Audit](#quick-start) | Does this Lean say what the source says? | Run on two real companions of one book |
| [PDF overlay](#pdf-overlay) | Where on each page is an item, and which declarations formalize it? | Generates the overlays of three private companions |
| [Locate](#locate) | Which passages of a source are formalized, in which library, and how closely? | Experimental |
| [Informalize](docs/proposals/informalize.md) | What does this Lean library's theorem say, in a text a reader can read? | Published [Seifert–van Kampen example](https://mathlib-initiative.github.io/seifert-van-kampen-tauceti/); [Status](docs/status.md) |

Audit and locate write only to a workspace outside this repository. `cf overlay` writes
only `pdf-overlay/` and `annotations/` in the companion, and `cf informalize` only the
repository it creates itself inside the workspace. `cf` never builds a companion or a
library, and it reports findings rather than fixing them.

## Install

```sh
SKILL=~/skills/formal-companion
git clone https://github.com/mathlib-initiative/formal-companion "$SKILL" &&
  mkdir -p ~/.local/bin &&
  ln -s "$SKILL/bin/cf" ~/.local/bin/cf
export PATH="$HOME/.local/bin:$PATH"
cf doctor
```

The shim resolves back to this checkout, so nothing is installed into Python. Add the
`export` line to your shell profile.

`cf doctor` checks for Python 3.11+, `git`, `curl` and poppler's `pdftotext` and `pdftoppm`.
Model work run as a Workflow needs a Claude Code session. A fleet needs ssh and rsync to a
host you may use, plus `tend` and `agentbox`, which do not ship here.

To load the repository as a Claude Code skill:

```sh
mkdir -p ~/.claude/skills && ln -sfn ~/skills/formal-companion ~/.claude/skills/companion-faithfulness
```

## Quick start

An audit works in a *workspace*: a local git repository with no remote, outside every
companion. The model-free tier runs at no cost:

```sh
COMPANION=/path/to/companion
NAME=$(basename "$COMPANION" | tr 'A-Z_ ' 'a-z--')
WORKSPACE=~/audit-workspace
cf init "$WORKSPACE" && cd "$WORKSPACE" && cf onboard "$COMPANION" && cf pair &&
  cf detect-proxies "$NAME" &&
  cf status
```

`cf onboard` infers the page offset and item kinds and looks for an errata page, and what
it cannot decide it refuses, naming the override. `cf tally --union`, `cf route` and
`cf report` then count, route and report the findings. With no companion of your own, use
the demo's `notes-b`, which sits beside the workspace its last line names.

The model tier reads each item with an agent. `cf` plans, gates and transcribes that work,
and the models run in a Workflow launched from a Claude Code session, or under `tend` on a
host. The two complete differential fleets on record cost about $7 a task at list prices,
probably double-counted ([Status](docs/status.md)). No attack preset has a recorded cost.
`cf plan-run` refuses until the owner has confirmed `companions/<name>/conventions.md`, the
list of what a companion does on purpose, or `--conventions-unconfirmed` records the override.

```sh
RUN=attack-vac-1
cf plan-run --preset attack-vac --companion "$NAME" --run-id "$RUN" &&
  cf run "$RUN" --local
```

`cf run --local` prints the script to launch from a Claude Code session. When that Workflow
has finished, `cf harvest "$RUN" --journal <its wf_* directory>` brings the findings back
through the gate. [Running an audit](docs/running-an-audit.md) has the rest.

## PDF overlay

A companion's overlay is a local viewer that shows the source PDF with each item outlined
and, beside it, the declarations that formalize the item. It works from
[Literature Links](https://github.com/mathlib-initiative/literature-links) 1.0 annotations,
which live in the companion's `annotations/`. The PDF is never committed: the tools fetch
it over HTTPS, verify its sha256, cache it under `.lake/` and serve it on loopback only. A
PDF under an open license may instead be published with the site (`pdf.publish`), so the
overlay also works from GitHub Pages.

[Generating an overlay](docs/generating-an-overlay.md) has the steps to create one, and the
browser limits. In a companion that has one, this builds its Verso site with `lake` and
serves the viewer:

```sh
cd "$COMPANION" && python3 pdf-overlay/tools/serve.py --build
```

## Locate

Locate asks the audit's question in reverse, with English Wikipedia's WikiProject
Mathematics as its first corpus. Nothing reaches Wikipedia without `--execute`. This driver
takes every stage over a made-up WikiProject and two made-up libraries, with no network or
language model:

```sh
cd ~/skills/formal-companion && python3 tests/e2e/locate_synthetic/driver.py
```

[SKILL.md](SKILL.md) has the commands in order, and
[docs/proposals/wikipedia-locate.md](docs/proposals/wikipedia-locate.md) the design.

## What has been exercised, and what has not

No second real book has had a full audit, and the repository holds no receipt of a completed
local Workflow fleet, the path the Quick start shows. [Status](docs/status.md) has what has
run, on what, and where the record is.

Never exercised end to end: locate's campaign sequencer and spend watchdog on a host, the
benchmark against real gold, and the `tend-agent` bridge, which is a stub. The commands
`fix`, `review-pr`, `camera-ready`, `publish-verdicts`, `curate` and `lint` are documented
and not implemented.

## Documentation

| Document | What it holds |
|---|---|
| [Running an audit](docs/running-an-audit.md) | Every stage in order, an onboarding transcript, the overrides |
| [The workspace](docs/workspace.md) | The directory model and its rules |
| [Onboarding contract](references/onboarding-contract.md) | What a companion must expose for `cf onboard` to succeed |
| [Adding an adapter](docs/adding-an-adapter.md) | Teaching `cf` a new companion convention |
| [Generating an overlay](docs/generating-an-overlay.md) | The overlay step by step, its refusals, the viewer |
| [Overlay contract](docs/overlay.md), [exporters](docs/exporters.md) | What a generated overlay must satisfy; what each export reads |
| [Status](docs/status.md) | What has run, on what, and where the record is |
| [SKILL.md](SKILL.md) | Each tool's commands in order for an agent, the hard rules |
| [Stage map](docs/design/stage_map.md) | The current spec: data model, on-disk layout, stages |
| [Measured method](references/method.md), [references/](references/) | The evidence behind the audit's design; vocabulary, schemas, traps, formats, runbook |
| [Repository layout](docs/layout.md), [Testing](docs/testing.md) | How the code fits together; test commands and CI |
| [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md), [POLICY.md](POLICY.md) | How to send a change, what a version promises, the copyright rule |

## License

Licensed under [Apache 2.0](LICENSE), which covers this code and the source metadata, never
the sources themselves ([POLICY.md](POLICY.md)).
