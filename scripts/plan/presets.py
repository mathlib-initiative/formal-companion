"""Run presets: the five original runs, plus the v2 and legacy variants.

Ported from the five orchestration roots retrieved from ffmain — see
``templates/PRESETS.md`` for the byte-level diff these values come from:

* ``tao-diff-tend``       -> ``differential-text``
* ``tao-diff-tend-pdf``   -> ``differential-pdf``
* ``tao-diff-tend-gpt``   -> ``differential-gpt``
* ``tao-attack-vac-tend`` -> ``attack-vac``
* ``tao-attack-hunt-tend``-> ``attack-hunt``

A preset is data, not code: everything that differed between those five roots
(gate KINDS/VERDICTS, witness requirement, book access, bridge, prompt blocks,
task shape, concurrency) is a field here, and ``plan-run`` writes it into
``run.yaml`` and ``gate.json`` so a run is reproducible from its own directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Mapping

from cflib import vocab
from cflib.errors import CfError

#: The three differential kinds gate-wise; PAGE_ANCHOR is mechanical-only in v2
#: (22 of 40 batch-1 differential findings were pagination noise).
DIFFERENTIAL_KINDS = sorted(vocab.KINDS_BY_MODE[vocab.DIFFERENTIAL])
ATTACK_KINDS = sorted(vocab.KINDS_BY_MODE[vocab.ATTACK_VAC])
LEGACY_DIFFERENTIAL_KINDS = sorted(set(DIFFERENTIAL_KINDS) | {vocab.PAGE_ANCHOR})

DIFFERENTIAL_VERDICTS = sorted(vocab.MATCH_VERDICTS - {vocab.NO_DEFECT_FOUND})
ATTACK_VERDICTS = sorted(vocab.MATCH_VERDICTS)
#: The vac run spelled it NO_WEAKNESS_FOUND; legacy presets accept both so the
#: 2,672-file baseline validates unchanged after convert_v1.
LEGACY_ATTACK_VERDICTS = sorted(set(ATTACK_VERDICTS) | {"NO_WEAKNESS_FOUND"})
LEGACY_DIFFERENTIAL_VERDICTS = sorted(set(DIFFERENTIAL_VERDICTS) | {"NO_WEAKNESS_FOUND", "NO_DEFECT_FOUND"})

#: Item kinds worth a worker's best slot (the recovered prioritiser: 200 max /
#: 223 high / 340 default over targets.prioritised.json).
KEY_KINDS = ("Definition", "Lemma", "Theorem", "Corollary", "Proposition", "In-text")
LATE_SECTIONS = ("1.5", "1.6", "1.7")


@dataclass(frozen=True)
class Preset:
    """One run configuration."""

    name: str
    mode: str                       # vocab.DIFFERENTIAL | vocab.ATTACK_VAC | vocab.ATTACK_HUNT
    kinds: tuple[str, ...]
    verdicts: tuple[str, ...]
    witness_required: bool
    book_access: str                # "text" | "text+images"
    bridge: str                     # agentbox-claude | agentbox-codex | claude-p | tend-agent
    model: str | None               # None = the bridge's own default (never recorded upstream)
    # prompt blocks (templates/prompts/*.tmpl)
    page_images: bool = False
    citation_ban: bool = False
    approve_guidance: bool = True
    final_line: bool = False        # codex bridges parse "VERDICT: approve" from free text
    # tasks
    task_style: str = "differential"  # differential | attack
    attack_label: str = ""            # "hunt" | "vac"
    slug_style: str = "underscore"    # underscore (tend task ids) | dash (attack findings)
    prioritise: bool = False
    # tend
    max_workers: int = 6
    max_reviewers: int = 4
    # gate
    kind_rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    require_finding_id: bool = False
    #: demand a verbatim book_passage_quote on every document.  Off for
    #: `mechanical`: a detector never read the book's prose.
    require_passage_quote: bool = True
    #: run the gate's operator pass (a flipped <=/>=/=/!= is refused).  Off only
    #: for the two *-legacy presets: the imported 2011-draft corpus carries
    #: pdftotext artifacts in two of its 3,019 quotes that predate the check.
    check_operators: bool = True
    legacy: bool = False
    note: str = ""

    @property
    def is_attack(self) -> bool:
        return self.mode in vocab.ATTACK_MODES

    @property
    def needs_images(self) -> bool:
        return self.book_access == "text+images"

    def to_dict(self) -> dict:
        return asdict(self)


#: PAGE_ANCHOR and COVERAGE as the legacy corpus actually carries them: a quote,
#: no structured page object, and — in two of the 1,146 differential rows
#: (findings-pdf/exercise-1_6_6, findings-gpt/exercise-1_6_44) — no declaration
#: at all, which is precisely why routing must key on `unfaithful_decls` and why
#: the v2 `mechanical` preset requires both fields.
_LEGACY_RULES = {
    "PAGE_ANCHOR": {"quote": True, "witness": False, "decls": False, "pages": False,
                    "mechanical_only": False},
    "COVERAGE": {"quote": False, "witness": False, "decls": False},
}

_DIFF_COMMON = dict(
    mode=vocab.DIFFERENTIAL,
    kinds=tuple(DIFFERENTIAL_KINDS),
    verdicts=tuple(DIFFERENTIAL_VERDICTS),
    witness_required=False,
    task_style="differential",
    slug_style="underscore",
    prioritise=False,
)

_ATTACK_COMMON = dict(
    kinds=tuple(ATTACK_KINDS),
    verdicts=tuple(ATTACK_VERDICTS),
    witness_required=True,
    citation_ban=True,
    task_style="attack",
    slug_style="dash",
    prioritise=True,
    max_workers=5,
    max_reviewers=3,
)

PRESETS: dict[str, Preset] = {
    "differential-text": Preset(
        name="differential-text",
        book_access="text",
        bridge="agentbox-claude",
        model=None,
        max_workers=8,
        max_reviewers=5,
        note="tao-diff-tend: 382 tasks, 224 DIFFERENTIAL + 158 SINGLE-SOURCE",
        **_DIFF_COMMON,
    ),
    "differential-pdf": Preset(
        name="differential-pdf",
        book_access="text+images",
        bridge="agentbox-claude",
        model=None,
        page_images=True,
        note="tao-diff-tend-pdf: same tasks, page images added for notation",
        **_DIFF_COMMON,
    ),
    "differential-gpt": Preset(
        name="differential-gpt",
        book_access="text",
        bridge="agentbox-codex",
        model="gpt-5.6-sol",
        final_line=True,
        note="tao-diff-tend-gpt: the calibration pair's second model",
        **_DIFF_COMMON,
    ),
    "attack-vac": Preset(
        name="attack-vac",
        mode=vocab.ATTACK_VAC,
        book_access="text",
        bridge="agentbox-codex",
        model="gpt-5.6-sol",
        final_line=True,
        attack_label="vac",
        note="tao-attack-vac: vacuity/triviality/strength/junk-value probe; 11 substantive findings",
        **_ATTACK_COMMON,
    ),
    "attack-hunt": Preset(
        name="attack-hunt",
        mode=vocab.ATTACK_HUNT,
        book_access="text+images",
        bridge="agentbox-claude",
        model=None,
        page_images=True,
        attack_label="hunt",
        note="tao-attack-hunt: adversarial defect hunt with page images; 6 substantive findings",
        **_ATTACK_COMMON,
    ),
    # ---- gate-only presets -------------------------------------------------
    "differential-legacy": Preset(
        name="differential-legacy",
        book_access="text",
        bridge="none",
        model=None,
        kind_rules=_LEGACY_RULES,
        check_operators=False,
        legacy=True,
        note="validates the converted v1 differential corpus (findings/, -pdf, -gpt); "
             "keeps PAGE_ANCHOR in KINDS because those runs emitted it",
        **{**_DIFF_COMMON,
           "kinds": tuple(LEGACY_DIFFERENTIAL_KINDS),
           "verdicts": tuple(LEGACY_DIFFERENTIAL_VERDICTS)},
    ),
    "attack-legacy": Preset(
        name="attack-legacy",
        mode=vocab.ATTACK_HUNT,
        book_access="text",
        bridge="none",
        model=None,
        attack_label="hunt",
        kind_rules=_LEGACY_RULES,
        check_operators=False,
        legacy=True,
        note="validates the converted v1 attack corpus (findings-vac/, findings-hunt/); "
             "the witness is the one convert_v1 lifted out of `reasoning`",
        **{**_ATTACK_COMMON, "verdicts": tuple(LEGACY_ATTACK_VERDICTS)},
    ),
    "mechanical": Preset(
        name="mechanical",
        mode=vocab.DIFFERENTIAL,
        kinds=(vocab.PAGE_ANCHOR, vocab.PROXY, vocab.COVERAGE),
        verdicts=tuple(DIFFERENTIAL_VERDICTS),
        witness_required=False,
        book_access="text",
        bridge="none",
        model=None,
        task_style="differential",
        slug_style="underscore",
        require_finding_id=True,
        require_passage_quote=False,
        note="the gate for machine-produced findings: pair/anchor_check.py and detect/",
    ),
}

#: Presets a fleet may actually be launched with.
RUNNABLE = tuple(p for p, v in PRESETS.items() if not v.legacy and v.bridge != "none")


def get(name: str) -> Preset:
    """Look up a preset by name, or raise with the list of valid names."""
    try:
        return PRESETS[name]
    except KeyError:
        raise CfError(
            f"unknown preset {name!r}",
            hint="one of: " + ", ".join(sorted(PRESETS)),
        ) from None


def priority_for(kind: str, single_source: bool, section: str | None) -> str:
    """The recovered priority rule (exact: 200 max / 223 high / 340 default).

    ``max``  — single-source AND a key kind: nothing else looks at it.
    ``high`` — a key kind with peers, or a single-source item in a late section.
    ``default`` — everything else.
    """
    key = kind in KEY_KINDS
    if single_source and key:
        return "max"
    if key or (single_source and (section or "") in LATE_SECTIONS):
        return "high"
    return "default"


def source_block(spec: "Mapping[str, Any] | str") -> dict:
    """Normalise one ``gate.json`` ``sources.<id>`` body.

    A caller may pass the whole mapping or just the text path; the gate needs
    the five keys below and nothing else, and a missing ``page_offset`` is 0
    rather than an exception because a source whose offset is unknown still has
    a quote gate worth running.
    """
    if isinstance(spec, str):
        spec = {"text": spec}
    return {
        "text": str(spec.get("text") or "") or None,
        "sha256": spec.get("sha256") or spec.get("source_sha") or None,
        "text_sha256": spec.get("text_sha256") or None,
        "images_dir": str(spec["images_dir"]) if spec.get("images_dir") else None,
        "page_offset": int(spec.get("page_offset") or spec.get("offset") or 0),
    }


def gate_config(
    preset: Preset,
    *,
    run_id: str,
    sources: "Mapping[str, Any]",
    repos: list[str],
    decl_index: str | None = None,
) -> dict:
    """Build the ``gate.json`` the out-of-tree gate reads.

    ``sources`` maps ``source_id`` -> ``{text, sha256, text_sha256, images_dir,
    page_offset}`` (docs/design/multi-source-model.md S6.2).  It is a MAP, not a
    single book, because a run may span more than one source and the gate picks
    the text to check a quote against by the document's own ``source_id`` --
    checking every quote against one concatenated text would let a quote from
    the 2011 draft pass as the published book's.
    """
    blocks = {str(sid): source_block(body) for sid, body in dict(sources or {}).items()}
    if not blocks:
        raise CfError(
            "gate_config() was given no sources",
            hint="a gate with no text to check quotes against passes everything; "
                 "pass sources={source_id: {text, sha256, ...}}",
        )
    return {
        "schema_version": 2,
        "preset": preset.name,
        "mode": "attack" if preset.is_attack else "differential",
        "run_id": run_id,
        "sources": blocks,
        "repos": list(repos),
        "kinds": list(preset.kinds),
        "verdicts": list(preset.verdicts),
        "severities": list(vocab.SEVERITIES),
        "witness_required": preset.witness_required,
        "require_book_located": True,
        "require_source_sha": True,
        "verify_text_sha": any(b["text_sha256"] for b in blocks.values()),
        "require_finding_id": preset.require_finding_id,
        "require_passage_quote": preset.require_passage_quote,
        "check_operators": preset.check_operators,
        # Bind every quote to the page its own item is stated on.  Off for the
        # *-legacy presets for the same reason as the operator pass: measured with
        # the source's own page_offset (16), 16 of the 2,985 bindable quotes of the
        # imported corpus, in 16 files, sit further than PAGE_BIND_WINDOW pages
        # from their item, and those rows are already landed.
        "bind_item_page": not preset.legacy,
        "findings_dir": "findings",
        "decl_index": decl_index,
        "kind_rules": preset.kind_rules or {},
    }
