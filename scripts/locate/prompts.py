"""Render a locate run's prompts: one system prompt per role, one task prompt per mode.

New code, for docs/proposals/wikipedia-locate.md sections 3, 5 and 6 (the host
tools "with the exact invocation rendered into the prompts").  The bridge
reads ``<role>-system.md`` and ``<role>.md`` from the prompt directory, so a
locate run writes those four names from ``templates/prompts/locate-*.md.tmpl``:

* ``worker-system.md`` -- ``locate-worker-system.md.tmpl``: the universe, the
  tools by absolute path, what the gate refuses, quotes and data hygiene;
* ``worker.md`` -- the mode's own task prompt (``locate-inventory.md.tmpl``,
  ``locate.md.tmpl`` or ``locate-challenge.md.tmpl``) with the document shape
  of references/locate-documents.md sections 2-4;
* ``reviewer-system.md`` and ``reviewer.md`` -- in an unaudited run, form and gate
  output only: judgment lives in the L5 challenge, not in the tend reviewer.  On a
  challenge plan (``is_challenge``) the rule card says that the reviewer's approval is
  final, that the gate counts the records the task owes (its refusals and its ``OK:``
  line quoted, a test holding them to the gate's words) and the conditional rule of
  references/locate-documents.md section 4: smoke 4's four reviewer rejections each asked
  for a verdict on a conditional record.

An **audited** inventory plan (``cf plan-locate --audit-rounds``, references/
locate-documents.md section 13) judges the cut in the review instead, so its reviewer
prompts are the audit's and it stages three more (:data:`AUDIT_TEMPLATES`):
``reviewer-system.md`` from ``locate-inventory-audit-system.md.tmpl``, the rule card
(the statement cut's rules 1-10 as docs/proposals/statement-cut.md section 3.4 numbers
them, C1 to C10, a test holding the two together), what the gate's bound allows
(:func:`gate_bound`) and the reviewer's tools; ``reviewer.md`` the auditor,
``reviewer-check.md`` the checker, ``reviewer-skeptic.md`` the skeptic, and
``worker-revision.md`` the repair, which ``audit_turn.py`` appends to ``worker.md``
in a revision session.  Their slots are the plan's (``{{...}}``) and the turn's
(``$CF_AUDIT_ROUND``, ``$CF_AUDIT_ROUNDS``, ``$CF_AUDIT_SOURCE``, ``$CF_AUDIT_DOCUMENT``,
section 13.9), and ``audit_turn.py`` appends each call's own block: what is staged, the
one file it writes and its validator line.  An unaudited plan renders the four prompts it
always rendered, byte for byte.  Audit rule 2's text (section 13.11: the skips, the skip
verdict, ``unskip``, ``--rule 2`` on the tools' command lines) sits in ``{{#if audit_skips}}``
blocks that render empty under rule 1, so a rule-1 plan's prompts are the ones the base
commit rendered.

Every invocation is rendered from the run's own host paths, and the renderer
(``plan.render``) refuses a template with an unfilled slot.

The templates describe the versions the planner writes into every gate.json
(docs/proposals/statement-cut.md section 7): the inventory's statement cut under
span-end rule 3 and statement-cut rule 3 (``vocab.SPAN_END_RULE`` and
``vocab.STATEMENT_CUT_RULE``: the continued end of inv-pilot-1's fixes and the wide
bound of inv-pilot-2's repairs) and judgment rules 3 (``vocab.JUDGMENT_RULES``).  The
worker's prompts ask for no end past the loose continued bound, which the wide bound
contains, so the flip of the wide bound changed none of them; what the wide bound adds
is said where a reviewer judges a merge, in the audit's rule card (:func:`gate_bound`).
The text they rendered for a plan made before the flip (the claim lists of judgment
rules 2, version 1's end paragraph) sat behind two slots that no plan made since could
render, and was removed with them; a change of any of these constants is a change to
these templates.
"""
from __future__ import annotations

import pathlib
from typing import Any, Mapping

from cflib import vocab
from cflib.paths import templates_dir

from plan.presets import Preset
from plan.render import render_file
from plan.render_prompts import finish_sentence

from . import presets as locate_presets

PROMPT_FILES = ("worker-system.md", "worker.md", "reviewer-system.md", "reviewer.md")
#: what an audited inventory plan renders each reviewer prompt and the revision from
#: (references/locate-documents.md section 13.9); its worker prompts are the unaudited ones
AUDIT_TEMPLATES = {"reviewer-system.md": "locate-inventory-audit-system.md.tmpl",
                   "reviewer.md": "locate-inventory-audit.md.tmpl",
                   "reviewer-check.md": "locate-inventory-check.md.tmpl",
                   "reviewer-skeptic.md": "locate-inventory-skeptic.md.tmpl",
                   "worker-revision.md": "locate-inventory-revision.md.tmpl"}
#: the seven prompt files of an audited plan, the turn machine's (``audit_turn.PROMPT_FILES``)
AUDIT_PROMPT_FILES = PROMPT_FILES + ("reviewer-check.md", "reviewer-skeptic.md",
                                     "worker-revision.md")

#: the locate gate's exit vocabulary and the lines it prints with each
#: (``scripts/gate/verify_locate.py``; a test pins these to the gate's own
#: constants and output).  The prompts read the gate by these and nothing else:
#: before the gate guarded its imports, a missing module came out as Python's
#: exit 1, which every prompt read as "your document is wrong" (field defect P2).
GATE_EXIT_REJECTED = 1
GATE_EXIT_BROKEN = 3
GATE_REJECTED_MARK = "VALIDATION FAILED"
GATE_BROKEN_MARK = "the gate could not run"


def gate_exits(role: str) -> str:
    """How a ``role`` reads the gate's exit status; one sentence for every locate prompt."""
    broken = (f"**exit {GATE_EXIT_BROKEN}** (its `FAIL: … {GATE_BROKEN_MARK}` line), any other "
              f"non-zero exit, or an exit {GATE_EXIT_REJECTED} without the "
              f"`{GATE_REJECTED_MARK}` list (a crash) means the gate itself could not run")
    if role == "reviewer":
        return (f"**exit {GATE_EXIT_REJECTED}**, which always comes with a "
                f"`{GATE_REJECTED_MARK}` list, is a verdict on the document: request changes "
                f"naming what it refused. {broken}: an infrastructure fault, not grounds for "
                "rejecting the worker; say so in your notes.")
    return (f"**exit 0** passes. **exit {GATE_EXIT_REJECTED}**, which always comes with a "
            f"`{GATE_REJECTED_MARK}` list, is a verdict on your document: fix what it names "
            f"and run the gate again. {broken}: an infrastructure fault, not a verdict on your "
            "work — stop, finish with status `blocked`, quote what the gate printed, and do not "
            "weaken your document to make it pass.")


def finish_block(preset: Preset) -> str:
    """The worker's one way to finish, on this preset's bridge."""
    files = ('["findings/<task id>/<source-id>.json", "tasks/<task id>.yaml"]')
    status = ("`status` is `completed` (documents written and the gate passes), "
              "`needs_review` (written, but you are unsure), or `blocked` (you could not do "
              "the work at all, or the gate itself was broken: say why in `summary`).")
    channel = preset.finish_channel
    if channel == "final_result":
        return ("## How to finish (required)\n"
                "Call the `final_result` tool **exactly once**, with schema_version=1, status "
                "(`completed`, `blocked` or `needs_review`), a non-empty summary, and the files "
                "you changed. This is the only channel that is read.\n")
    shape = ('{"schema_version": 1, "status": "completed", "summary": "one or two sentences", '
             f'"files_changed": {files}}}')
    if channel == "stdout":
        return ("## How to finish (required)\n"
                "There is no `final_result` tool on this bridge and no result file is read. "
                "Finish by printing **one JSON object as the last thing in your output**, after "
                f"committing:\n\n{shape}\n\n{status}\n")
    return ("## How to finish (required)\n"
            "Your stdout is not read, and there is no `final_result` tool on this bridge. "
            "Finish by writing **`.tend/agent_result.json`** in the worktree, last, after "
            f"committing:\n\n{shape}\n\n{status}\n")


def phrase_list(phrases) -> str:
    """A judgment-rule phrase list as a prompt says it: quoted, ``*`` written as ``…``."""
    return ", ".join(f'"{p[:-1]}…"' if p.endswith("*") else f'"{p}"' for p in phrases)


def prompt_files(audited: bool = False) -> tuple[str, ...]:
    """The prompt files a plan renders: the four, or an audited plan's seven."""
    return AUDIT_PROMPT_FILES if audited else PROMPT_FILES


#: what the gate's bound lets a passage cross, by the statement-cut rule a plan writes
#: (docs/proposals/statement-cut.md sections 3.1 and 3.3): the rule card says it, so an
#: auditor asks for no merge the gate refuses without saying so
_GATE_BOUNDS = {
    vocab.STATEMENT_CUT_CONTINUED: (
        "The gate holds a passage to its block (a paragraph, a list item, a theorem box), "
        "except that it runs on into the displays its last sentence introduces and the "
        "paragraph that finishes that sentence after them, and over the items of one list "
        "of a compound statement (statement-cut rule {rule}). A merge that needs more than "
        "that is refused at the gate: say so in its reason."),
    vocab.STATEMENT_CUT_WIDE: (
        "The gate holds a passage to its block (a paragraph, a list item, a theorem box), "
        "except that it runs on into the displays its last sentence introduces and the block "
        "that finishes that sentence after them, and over the items of one list of a compound "
        "statement (statement-cut rule {rule}). Finishing the sentence includes crossing a "
        "footnote mark between the blocks, and a `dd` or paragraph whose first word goes on "
        "with the sentence (lower-case, or a formula followed by a lower-case word after a "
        "sentence that ends with a word), from a paragraph, a `dd` or a display but never "
        "from a `dt` term or a quotation. A text-only `dd` that starts with a capital, and a "
        "capitalised \"Then\" between two displays, stay refused. A merge that needs more "
        "than that is refused at the gate: say so in its reason."),
}


def gate_bound(rule: int | None = None) -> str:
    """The rule card's "What the gate allows", for the statement-cut rule a plan writes."""
    rule = vocab.STATEMENT_CUT_RULE if rule is None else rule
    if rule not in _GATE_BOUNDS:
        raise ValueError(f"no rule card describes statement-cut rule {rule}")
    return _GATE_BOUNDS[rule].format(rule=rule)


def _ticks(values) -> str:
    return ", ".join(f"`{v}`" for v in values)


def audit_context(host: Mapping[str, str], review_rounds: int) -> dict[str, Any]:
    """The slots only the audit's templates read (references/locate-documents.md section 13).

    The audit rule is the one a new plan writes (``vocab.INVENTORY_AUDIT_RULE``); under rule
    2 ``audit_skips`` keeps the templates' skip blocks (section 13.11), which render empty
    under rule 1, so a rule-1 plan's prompts are the base commit's byte for byte."""
    from .tend_root import audit_commands
    cmd = audit_commands(host["gate_dir"])
    rule = vocab.INVENTORY_AUDIT_RULE
    skips = rule >= 2
    return {
        "audited": True,
        "review_rounds": review_rounds,
        "audit_rule": rule,
        "audit_schema": vocab.INVENTORY_AUDIT_SCHEMAS[rule],
        "audit_skips": skips,
        "skip_verdict": vocab.AUDIT_SKIP_VERDICT,
        "skip_statements_max": vocab.AUDIT_SKIP_STATEMENTS_MAX,
        "skip_at_min": vocab.AUDIT_SKIP_AT_WORDS[0],
        "skip_at_max": vocab.AUDIT_SKIP_AT_WORDS[1],
        "skip_until_words": vocab.AUDIT_SKIP_UNTIL_WORDS,
        "prompts_dir": host["prompts_dir"],
        "passage_command": cmd["show"],
        "edit_command": cmd["edit"],
        "validate_command": cmd["validate"],
        "gate_bound": gate_bound(),
        "audit_verdicts": _ticks(v for v in vocab.AUDIT_VERDICTS if v != "should_be_formal"),
        "audit_origins": _ticks(vocab.AUDIT_ORIGINS),
        "check_statuses": _ticks(vocab.AUDIT_CHECK_STATUSES),
        "repair_statuses": _ticks(vocab.AUDIT_REPAIR_STATUSES),
        "edit_ops": _ticks(vocab.AUDIT_EDIT_OPS + (vocab.AUDIT_SKIP_OPS if skips else ())),
        "edit_fields": _ticks(vocab.AUDIT_EDIT_FIELDS),
        "reason_words": vocab.AUDIT_REASON_WORDS,
        "action_words": vocab.AUDIT_ACTION_WORDS,
        "evidence_words": vocab.AUDIT_EVIDENCE_WORDS,
        "summary_words": vocab.AUDIT_SUMMARY_WORDS,
        "max_patterns": vocab.AUDIT_MAX_PATTERNS,
        "skeptic_floor": vocab.AUDIT_SKEPTIC_FLOOR,
        "kind_rule_rule": vocab.AUDIT_KIND_RULE_RULE,
        "verdicts_per_session": vocab.AUDIT_REPAIR_VERDICTS_PER_SESSION,
    }


def build_context(preset: Preset, *, run_id: str, host: Mapping[str, str],
                  universe, per_task: int, merge_target_branch: str = "main",
                  audit_rounds: int | None = None) -> dict[str, Any]:
    """Every slot the locate templates read, from the run's host paths and its universe.

    ``audit_rounds`` (an audited inventory plan's R) adds the audit's slots
    (:func:`audit_context`); the unaudited templates read none of them.
    """
    gate_dir = host["gate_dir"]
    # the TOOLS database (references/locate-documents.md section 9): the one the
    # allowlist pins the two tools to, never the gate's check database
    db = host["tools_corpus_db"]
    lines = []
    for lid, lib in sorted(universe.libraries.items()):
        lines.append(f"- `{lid}` — {lib.get('repo') or '(no repo url)'} at "
                     f"`{str(lib['commit'])[:12]}`")
    channel = preset.finish_channel
    ctx = {
        "run_id": run_id,
        "mode": preset.mode,
        "schema": vocab.LOCATE_DOCUMENT_SCHEMA,
        "merge_target_branch": merge_target_branch,
        "universe_id": universe.id,
        "universe_sha256": universe.sha256,
        "universe_sha8": universe.sha256[:8],
        "universe_lines": "\n".join(lines),
        "gate_command": host["gate_command"],
        "search_command": f"python3 {gate_dir}/corpus_search.py --db {db}",
        "show_command": f"python3 {gate_dir}/corpus_show.py --db {db}",
        "ids_command": f"python3 {gate_dir}/passage_ids.py",
        "sources_dir": host["sources_dir"],
        "candidates_dir": host["candidates_dir"],
        "tasks_dir": host["tasks_dir"],
        "checkouts_dir": host.get("checkouts_dir") or "",
        "max_locations": vocab.MAX_LOCATIONS_PER_PASSAGE,
        "min_queries": vocab.MIN_ABSENCE_QUERIES,
        "min_coverage_parts": vocab.MIN_COVERAGE_PARTS,
        # the judgment rules' phrase list (cflib.vocab), rendered so a prompt never
        # lists fewer words than the gate refuses
        "generality_phrases": phrase_list(vocab.CLOSE_MATCH_GENERALITY_PHRASES),
        # the statement cut's slots (docs/proposals/statement-cut.md section 6): the
        # inventory, locate and challenge prompts render them, so they list what
        # cflib.vocab defines
        "generality_phrases_statement_cut": phrase_list(
            vocab.CLOSE_MATCH_GENERALITY_PHRASES_STATEMENT_CUT),
        "compound_shapes": ", ".join(f"`{s}`" for s in vocab.COMPOUND_SHAPE_ORDER),
        "min_conjuncts": vocab.MIN_CONJUNCTS,
        "max_conjuncts": vocab.MAX_CONJUNCTS,
        "cut_connectives": ", ".join(f'"{c}"' for c in vocab.CUT_CONNECTIVES),
        "cut_flags": ", ".join(f"`{f}`" for f in sorted(vocab.CUT_FLAGS)),
        "simple_statement_clause": vocab.SIMPLE_STATEMENT_CLAUSE,
        "per_task": per_task,
        "batch": vocab.CHALLENGE_BATCH_RECORDS,
        "is_inventory": preset.mode == vocab.LOCATE_INVENTORY,
        # the challenge reviewer's rule card says the conditional rule and that its
        # approval is final (references/locate-documents.md section 4)
        "is_challenge": preset.mode == vocab.LOCATE_CHALLENGE,
        "gate_exits": gate_exits("worker"),
        "gate_exits_reviewer": gate_exits("reviewer"),
        "finish_sentence": finish_sentence(preset),
        "finish_block": finish_block(preset),
        "finish_via_tool": channel == "final_result",
        "finish_via_file": channel == "agent_result",
        "finish_via_stdout": channel == "stdout",
    }
    if audit_rounds is not None:
        ctx.update(audit_context(host, audit_rounds))
    return ctx


def template_files(preset: Preset, audited: bool = False) -> dict[str, str]:
    """``{prompt name: the templates/prompts/ file it is rendered from}`` for one preset.

    ``run.yaml``'s ``locate.config_shas.prompts`` hashes these files, so a run's
    prompt configuration is pinned apart from the paths rendered into it.  An audited
    plan (an inventory's only) renders its reviewer prompts and the revision from
    :data:`AUDIT_TEMPLATES`.
    """
    out = {"worker-system.md": "locate-worker-system.md.tmpl",
           "worker.md": locate_presets.shape(preset.name).worker_template,
           "reviewer-system.md": "locate-reviewer-system.md.tmpl",
           "reviewer.md": "locate-reviewer.md.tmpl"}
    if audited:
        if preset.mode != vocab.LOCATE_INVENTORY:
            raise ValueError(f"an audit reviews an inventory, not a {preset.mode} run")
        out.update(AUDIT_TEMPLATES)
    return out


def render_prompts(preset: Preset, ctx: Mapping[str, Any], out_dir: pathlib.Path | None = None,
                   *, tdir: pathlib.Path | None = None) -> dict[str, str]:
    """The prompts of one locate run, the four or an audited plan's seven (``ctx`` from
    :func:`build_context`, ``audited`` with the audit's slots); written to ``out_dir`` when
    given."""
    tdir = pathlib.Path(tdir or (templates_dir() / "prompts"))
    out = {name: render_file(tdir / template, ctx)
           for name, template in template_files(preset, bool(ctx.get("audited"))).items()}
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, text in out.items():
            (out_dir / name).write_text(text, encoding="utf-8")
    return out
