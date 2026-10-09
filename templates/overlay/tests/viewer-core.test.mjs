/**
 * The overlay viewer's pure half, against synthetic shards and configs.
 *
 * Everything the contract (docs/overlay.md section 4) says the viewer builds
 * from the shards is checked here: the shard list, the refusals, geometry,
 * link ordering, one card per link however many regions repeat it, status and
 * note, the href rules, verdict display and sha256 verification. The
 * controller tests are in viewer-controller.test.mjs.
 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import {
  COVERAGE_SUMMARIES,
  COVERAGE_VALUES,
  DEFAULT_BACK_LINK,
  LINK_ROLES,
  NAMESPACE,
  VERDICT_KINDS,
  VERDICT_LABELS,
  buildIndex,
  cardSummaryText,
  cardsMatching,
  coverageSummary,
  declarationVerdictDisplay,
  groupCards,
  locatorText,
  paginateCards,
  parseShard,
  pdfSource,
  pointToViewport,
  preferredItemId,
  provenanceLabel,
  provenanceText,
  quadToViewport,
  readConfig,
  readShardList,
  recordRegion,
  resolutionText,
  safeBackLinkHref,
  safeHttpsUrl,
  sha256Hex,
  siteRootFor,
  targetHref,
  verdictDisplay,
  verdictNote,
  verifyPdfBytes,
} from "../runtime/viewer/viewer-core.js";
import {
  ORIGIN,
  OTHER_SOURCE_ID,
  PDF_BYTES,
  PDF_ROUTE,
  SOURCE_ID,
  VIEWER_DIR,
  fakeSha256,
  fakeViewport,
  makeConfig,
  makeGenerated,
  makeShards,
  shardInputs,
  shardText,
} from "./fixtures.mjs";

const PDF_SHA256 = await sha256Hex(PDF_BYTES);

function fixture(shards = makeShards(PDF_SHA256), configOverrides = {}) {
  const config = readConfig(makeConfig(configOverrides));
  return { config, index: buildIndex(shardInputs(shards), config, { pageOrigin: ORIGIN }) };
}

function cardsOf(index, itemId) {
  return index.items.get(itemId).cards;
}

// --- the configuration ------------------------------------------------------

test("config: the viewer reads what it needs and fills section 3's defaults", () => {
  const config = readConfig(makeConfig());
  assert.equal(config.sourceId, SOURCE_ID);
  assert.equal(config.pdfRoute, PDF_ROUTE);
  assert.equal(config.siteRoot, "../../");
  assert.equal(config.stripPrefix, "Notes.");
  assert.equal(config.storageKey, "notes-overlay-page");
  const bare = readConfig({ pdf: { route: PDF_ROUTE }, schema: "formal-companion/overlay-config",
                            schema_version: 1, source_id: SOURCE_ID, title: "T" });
  assert.deepEqual(bare.backLink, DEFAULT_BACK_LINK);
  assert.equal(bare.siteRoot, "../../", "the default mount is one directory deep");
  assert.equal(readConfig(makeConfig({ site: { mount: "tools/pdf-overlay" } })).siteRoot, "../../../");
});

test("config: a wrong schema, version, missing field, unsafe route or mount is refused", () => {
  for (const [override, pattern] of [
    [{ schema: "x" }, /has schema "x"/],
    [{ schema_version: 2 }, /schema_version 2/],
    [{ source_id: "" }, /no source_id/],
    [{ title: " " }, /no title/],
    [{ pdf: { route: "/files/notes.pdf" } }, /pdf\.route/],
    [{ pdf: { route: "/__a/../b.pdf" } }, /pdf\.route/],
    [{ site: { mount: "../outside" } }, /site\.mount/],
  ]) {
    assert.throws(() => readConfig(makeConfig(override)), pattern);
  }
  assert.throws(() => siteRootFor(""), /site\.mount/);
});

// --- the shard list ---------------------------------------------------------

test("shard list: the paths and sha256 come from GENERATED.json's annotations block", async () => {
  const generated = await makeGenerated(makeShards(PDF_SHA256));
  const list = readShardList(generated);
  assert.equal(list.directory, "annotations");
  assert.deepEqual(list.shards.map((row) => row.path), ["annotations/1.jsonl", "annotations/2.jsonl"]);
  assert.match(list.shards[0].sha256, /^[a-f\d]{64}$/);
});

test("shard list: an absent block, an empty list or an unsafe path is refused", () => {
  const row = { path: "annotations/1.jsonl", sha256: fakeSha256("a") };
  for (const [generated, pattern] of [
    [{}, /no annotations block/],
    [{ annotations: { directory: "../x", shards: [row] } }, /annotations\.directory/],
    [{ annotations: { directory: "annotations", shards: [] } }, /lists no shard/],
    [{ annotations: { directory: "annotations", shards: [{ ...row, path: "annotations/../x.jsonl" }] } },
     /shards\[0\]/],
    [{ annotations: { directory: "annotations", shards: [{ ...row, path: "other/1.jsonl" }] } }, /shards\[0\]/],
    [{ annotations: { directory: "annotations", shards: [{ ...row, path: "annotations/1.json" }] } },
     /shards\[0\]/],
    [{ annotations: { directory: "annotations", shards: [{ ...row, sha256: "x" }] } }, /shards\[0\]/],
  ]) {
    assert.throws(() => readShardList(generated), pattern);
  }
});

test("shards: one annotation per nonblank line, and anything else names its line", () => {
  const records = makeShards(PDF_SHA256)["annotations/1.jsonl"];
  assert.deepEqual(parseShard(`\n${shardText(records)}  \n`, "a.jsonl"), records);
  assert.throws(() => parseShard('{"a": 1}\n{oops\n', "a.jsonl"), /a\.jsonl:2 is not JSON/);
  assert.throws(() => parseShard("[1]\n", "a.jsonl"), /a\.jsonl:1 is not an annotation object/);
});

// --- the source -------------------------------------------------------------

test("source: the PDF is the records' source the config names, one sha256 for all", () => {
  const shards = shardInputs(makeShards(PDF_SHA256));
  assert.equal(pdfSource(shards, SOURCE_ID).pin.sha256, PDF_SHA256);
  assert.equal(pdfSource(shards, OTHER_SOURCE_ID).pin.sha256, fakeSha256("e"));
  assert.throws(() => pdfSource(shards, "src:nowhere"), /No shard holds a record of the PDF "src:nowhere"/);
  const split = makeShards(PDF_SHA256);
  split["annotations/2.jsonl"][0].source = { ...split["annotations/2.jsonl"][0].source,
                                            pin: { sha256: fakeSha256("1"), type: "book" } };
  assert.throws(() => pdfSource(shardInputs(split), SOURCE_ID), /two different PDFs/);
  const unpinned = makeShards("not a digest");
  assert.throws(() => pdfSource(shardInputs(unpinned), SOURCE_ID), /pins no sha256/);
});

// --- geometry ---------------------------------------------------------------

test("geometry: a rect is [x, y, width, height] from the top left", () => {
  assert.deepEqual(recordRegion({ location: { page: 2, rect: [0.1, 0.2, 0.5, 0.25] } }), {
    page: 2,
    quads: [[[0.1, 0.2], [0.6, 0.2], [0.6, 0.45], [0.1, 0.45]]],
  });
});

test("geometry: the extension's quads replace the bounding rect", () => {
  const record = { extensions: { [NAMESPACE]: { quads: [[0.1, 0.2, 0.9, 0.2, 0.9, 0.3, 0.1, 0.3]] } },
                   location: { page: 3, rect: [0.1, 0.2, 0.8, 0.1] } };
  assert.deepEqual(recordRegion(record).quads, [[[0.1, 0.2], [0.9, 0.2], [0.9, 0.3], [0.1, 0.3]]]);
});

test("geometry: a malformed, empty or off-page location is refused", () => {
  for (const [location, pattern] of [
    [undefined, /no physical page/],
    [{ page: 0, rect: [0, 0, 1, 1] }, /no physical page/],
    [{ page: 1 }, /no rect/],
    [{ page: 1, rect: [0, 0, 1] }, /no rect/],
    [{ page: 1, rect: [0, 0, 0, 0.5] }, /no area/],
    [{ page: 1, rect: [0.6, 0, 0.5, 0.5] }, /off the page/],
    [{ page: 1, rect: [0, 0, 1.2, 0.5] }, /no rect/],
  ]) {
    assert.throws(() => recordRegion({ location }), pattern);
  }
  const bad = { extensions: { [NAMESPACE]: { quads: [[0, 0, 1]] } }, location: { page: 1, rect: [0, 0, 1, 1] } };
  assert.throws(() => recordRegion(bad), /malformed quad/);
});

test("geometry: a page fraction lands in the viewport through the displayed page", () => {
  const displayed = fakeViewport();
  assert.deepEqual(pointToViewport([0.5, 0.25], displayed, fakeViewport({ scale: 2 })), [600, 400]);
  assert.deepEqual(quadToViewport([[0, 0], [1, 0], [1, 1], [0, 1]], displayed, fakeViewport()),
                   [[0, 0], [600, 0], [600, 800], [0, 800]]);
  // A page displayed rotated: fractions are of the rotated page, mapped back through PDF space.
  const rotated = {
    convertToPdfPoint: (x, y) => [y, x],
    height: 600,
    width: 800,
  };
  assert.deepEqual(pointToViewport([0.25, 0.5], rotated, fakeViewport()), [300, 600]);
  assert.throws(() => pointToViewport([1.5, 0], displayed, fakeViewport()), /page fractions/);
  assert.throws(() => pointToViewport([0, 0], {}, fakeViewport()), /displayed page viewport/);
  assert.throws(() => pointToViewport([0, 0], displayed, {}), /PDF\.js viewport/);
});

// --- the index --------------------------------------------------------------

test("index: pages carry the regions of this PDF only, and name their items", () => {
  const { index } = fixture();
  assert.equal(index.pageCount, 12);
  assert.equal(index.sha256, PDF_SHA256);
  assert.equal(index.bytes, PDF_BYTES.byteLength);
  assert.equal(index.sourceTitle, "Elementary Sets: Lecture Notes (synthetic)");
  assert.deepEqual([...index.pages.keys()].sort(), [2, 3, 5, 6, 7]);
  assert.deepEqual(index.pages.get(2).map((region) => [region.id, region.label]), [
    ["loc:0001", "Theorem 1.1"],
    ["loc:0004", "A remark on unions + Theorem 1.1"],
  ]);
  assert.equal(index.warnings.length, 0);
  assert.equal(index.regions.some((region) => region.itemIds.includes("elsewhere")), false);
});

test("index: a region lists its items in the curation's order, not the shards'", () => {
  const { index } = fixture();
  const [region] = index.pages.get(7);
  assert.deepEqual(region.itemIds, ["item:remark-7.1", "item:equation-7"],
                   "region_items names the remark first; the shard holds the equation first");
  assert.equal(region.label, "Remark 7.1 and equation (7) + Equation 7");
  assert.equal(preferredItemId(region, index.items), "item:remark-7.1");
});

test("index: a click opens the first item with a card, else the first item", () => {
  const items = new Map([["a", { cards: [] }], ["b", { cards: [{}] }], ["c", { cards: [{}] }]]);
  assert.equal(preferredItemId({ itemIds: ["a", "b", "c"] }, items), "b");
  assert.equal(preferredItemId({ itemIds: ["c", "b"] }, items), "c");
  assert.equal(preferredItemId({ itemIds: ["a"] }, items), "a");
  assert.equal(preferredItemId({ itemIds: [] }, items), null);
});

test("index: a record that cannot be placed is dropped with a warning, not guessed", () => {
  const shards = makeShards(PDF_SHA256);
  shards["annotations/2.jsonl"][0].location = { page: 5, rect: [0.9, 0.1, 0.4, 0.1] };
  shards["annotations/1.jsonl"][5].location = { page: 40, rect: [0.1, 0.1, 0.1, 0.1] };
  const { index } = fixture(shards);
  assert.equal(index.warnings.length, 2);
  assert.match(index.warnings.join("\n"), /item:exercise-3\.1: .*off the page/);
  assert.match(index.warnings.join("\n"), /page 40 is past the source's 12 pages/);
  assert.equal(index.pages.has(5), false);
});

test("index: one card per link, ordered key, attributed, support, example, helper", () => {
  const { index } = fixture();
  const cards = cardsOf(index, "item:theorem-1.1");
  assert.deepEqual(cards.map((card) => card.role), LINK_ROLES,
                   "two regions repeat each link, and the item still has five cards");
  assert.deepEqual(index.items.get("item:theorem-1.1").roles, LINK_ROLES);
});

test("index: a card carries its snippet, link, badges and declaration verdict", () => {
  const { index } = fixture();
  const [key, attributed, support, example, helper] = cardsOf(index, "item:theorem-1.1");
  assert.equal(key.name, "Box.volume_nonneg");
  assert.equal(key.fullName, "Notes.Box.volume_nonneg");
  assert.equal(key.module, "Notes.Box");
  assert.equal(key.href, `${ORIGIN}/Notes/Box/index.html#Notes___Box___volume_nonneg`,
               "a relative target resolves against the shard, which sits at the site root");
  assert.deepEqual(key.snippet, { line: 12, text: "theorem volume_nonneg (b : Box) : 0 <= b.volume" });
  assert.deepEqual(key.verdict, { note: "repaired in place", status: "fixed", verdict: "faithful" });
  assert.equal(key.relation, "exact");
  assert.deepEqual(key.badges, []);
  assert.equal(attributed.href, `${ORIGIN}/Notes/Box/index.html`);
  assert.deepEqual(attributed.badges, ["fallback"]);
  assert.equal(attributed.fallbackLabel, "module page (fallback)");
  assert.equal(support.href, "https://notes.example.invalid/api/Notes/Set.html#Notes___Set___inter_sub");
  assert.equal(example.href, null, "a target with no rendered page is not a link");
  assert.deepEqual(example.badges, ["anonymous", "unlinked"]);
  assert.equal(example.fullName, "example:0123456789ab");
  assert.deepEqual(helper.badges, ["sorry: exercise"]);
  const [aux] = cardsOf(index, "item:remark-4.1");
  assert.deepEqual(aux.badges, ["private"]);
  assert.equal(aux.declarationId, "private:Notes.Internal:Notes.Internal.aux");
});

test("index: a record from elsewhere, with no overlay extension, still shows as a link", () => {
  const { index } = fixture();
  const item = index.items.get("plain-link");
  assert.equal(item.label, "Lemma 6.1");
  assert.equal(item.cards[0].href, `${ORIGIN}/Notes/Plain/index.html`);
  assert.equal(index.pages.get(6)[0].id, "p6:0.2,0.2,0.2,0.2");
});

test("index: status, note, verdicts, locator and clusters ride on the item", () => {
  const { index } = fixture();
  const definition = index.items.get("item:definition-2.1");
  assert.equal(definition.coverage, "support_only");
  assert.equal(definition.coverageNote, "The companion states the neighbouring box lemma, not this definition.");
  const theorem = index.items.get("item:theorem-1.1");
  assert.equal(theorem.coverage, "formalized");
  assert.equal(theorem.locator, "Chapter 1, Theorem 1.1");
  assert.equal(definition.locator, "printed p. 1, PDF p. 3");
  assert.equal(theorem.verdicts.length, 1);
  const exercise = index.items.get("item:exercise-3.1");
  assert.deepEqual(exercise.cards, []);
  assert.deepEqual(exercise.clusterIds, ["cluster:sets"]);
  assert.equal(exercise.coverage, "");
  const absent = makeShards(PDF_SHA256);
  absent["annotations/2.jsonl"][0].status = { pin: { type: "repository" }, value: "not_formalized" };
  assert.equal(fixture(absent).index.items.get("item:exercise-3.1").coverage, "not_formalized");
});

test("locator: the cluster locator, else the printed and physical pages", () => {
  assert.equal(locatorText({ physical: [21, 25], printed: [5, 9], text: "Section 1.1, Definition 1.1.1" }),
               "Section 1.1, Definition 1.1.1");
  assert.equal(locatorText({ physical: [21, 25], printed: [5, 9] }), "printed pp. 5, 9, PDF pp. 21, 25");
  assert.equal(locatorText({ physical: [3] }), "PDF p. 3");
  assert.equal(locatorText(undefined), "");
});

test("provenance: each card carries its link's, and the item line counts key and attributed", () => {
  const { index } = fixture();
  const theorem = index.items.get("item:theorem-1.1");
  assert.deepEqual(theorem.cards.map((card) => card.provenance), [
    "curation, unconfirmed", "curation, unconfirmed", "curation, unconfirmed", "pipeline, confirmed",
    "curation, unconfirmed",
  ]);
  assert.equal(provenanceText(theorem),
               "1 key declaration for this result (2 attributed); curation, unconfirmed; pipeline, confirmed.");
  assert.equal(provenanceText(index.items.get("item:definition-2.1")), "", "no provenance, no line");
  assert.equal(provenanceText({ cards: [{ provenance: "curation, unconfirmed", role: "support" }] }),
               "Links: curation, unconfirmed.");
  assert.equal(provenanceLabel({ method: "curation" }), "curation");
  assert.equal(provenanceLabel({ confirmed_by_human: true }), "");
});

// --- coverage and the summary line ------------------------------------------

test("coverage: every value has its own one-line summary, and an unknown one has none", () => {
  for (const value of COVERAGE_VALUES) {
    assert.ok(coverageSummary(value), `${value} has no summary`);
    assert.equal(coverageSummary(value), COVERAGE_SUMMARIES[value]);
    assert.match(coverageSummary(value), /\.$/, `${value}'s summary is not a sentence`);
  }
  for (const value of ["formalized", "partial", "not_formalized", "support_only", "example_only",
                       "private_only", "out_of_scope"]) {
    assert.ok(COVERAGE_VALUES.includes(value), `${value} is missing`);
  }
  assert.equal(coverageSummary("mostly_formalised"), "");
  assert.equal(coverageSummary(undefined), "");
});

test("coverage: the stylesheet has a colour for every value and every verdict kind", async () => {
  const css = await readFile(path.join(VIEWER_DIR, "viewer.css"), "utf8");
  for (const value of COVERAGE_VALUES) {
    assert.ok(css.includes(`.target-summary.coverage-${value}`), `viewer.css has no rule for coverage ${value}`);
  }
  for (const kind of VERDICT_KINDS) {
    assert.ok(VERDICT_LABELS[kind], `${kind} has no label`);
    assert.ok(css.includes(`.verdict-badge.verdict-${kind}`), `viewer.css has no rule for verdict ${kind}`);
  }
  assert.ok(css.includes(".verdict-badge.verdict-fixed"));
});

test("summary: the line above the cards states the status or a count", () => {
  assert.equal(cardSummaryText({ coverage: "not_formalized", shown: 0 }), "Not formalised in this companion.");
  assert.equal(cardSummaryText({ shown: 5 }), "5 linked declarations.");
  assert.equal(cardSummaryText({ shown: 1 }), "1 linked declaration.");
  assert.equal(cardSummaryText({ shown: 0 }), "No declaration is linked to this item.");
  assert.equal(cardSummaryText({ coverage: "a-new-word", shown: 2 }), "2 linked declarations.");
});

// --- hrefs ------------------------------------------------------------------

test("href: a target resolves against its shard and must be https or this site", () => {
  const shard = `${ORIGIN}/annotations/1.jsonl`;
  assert.equal(targetHref("../A/index.html#x", shard, ORIGIN), `${ORIGIN}/A/index.html#x`);
  assert.equal(targetHref({ uri: "B.html" }, shard, ORIGIN), `${ORIGIN}/annotations/B.html`);
  assert.equal(targetHref("https://docs.example.invalid/A.html#a%22b", shard, ORIGIN),
               "https://docs.example.invalid/A.html#a%22b");
  for (const bad of ["javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,x",
                     "http://elsewhere.example.invalid/x", "//elsewhere.example.invalid/x",
                     "https://user:pw@docs.example.invalid/", "a b.html", "a\\b.html", "", null, 7, {}]) {
    assert.equal(targetHref(bad, shard, ORIGIN), null, `${JSON.stringify(bad)} must not be a link`);
  }
  // A plain-http site is this site only when the page is served from it.
  assert.equal(targetHref("../A.html", shard, "http://other.invalid:1"), null);
  assert.equal(safeHttpsUrl("https://a.invalid/x"), "https://a.invalid/x");
  assert.equal(safeHttpsUrl("https://a.invalid/x#y"), null);
});

test("href: the back link keeps its .. but never a scheme the config smuggled in", () => {
  assert.equal(safeBackLinkHref("../../index.html"), "../../index.html");
  assert.equal(safeBackLinkHref("https://notes.example.invalid/"), "https://notes.example.invalid/");
  for (const bad of ["javascript:alert(1)", "data:text/html,<script>x</script>", "http://notes.example.invalid/",
                     "//notes.example.invalid/x.html", "/site/index.html", "..\\..\\index.html",
                     "index.html#top", "   ", "", null]) {
    assert.equal(safeBackLinkHref(bad), null, `${bad} must be refused`);
  }
  const evil = readConfig(makeConfig({ back_link: { href: "javascript:alert(1)", text: "Back" } }));
  assert.equal(evil.backLink.href, DEFAULT_BACK_LINK.href);
  assert.equal(evil.backLink.text, "Back");
});

// --- verdicts ---------------------------------------------------------------

test("verdicts: a fixed entry reads as history, an open one keeps its kind", () => {
  assert.deepEqual(verdictDisplay("deviation", "fixed"),
                   { className: "verdict-fixed", label: "fixed: statement deviation" });
  assert.deepEqual(verdictDisplay("deviation", "unconfirmed"),
                   { className: "verdict-deviation", label: "statement deviation" });
  assert.deepEqual(verdictDisplay("attribution_gap", "contested"),
                   { className: "verdict-attribution_gap", label: "attribution gap" });
});

test("verdicts: a repaired declaration reads faithful and an unchanged one superseded", () => {
  assert.deepEqual(declarationVerdictDisplay("faithful", "fixed"), { className: "verdict-faithful", label: "faithful" });
  assert.deepEqual(declarationVerdictDisplay("deviation", "fixed"),
                   { className: "verdict-deviation", label: "statement deviation, superseded" });
  assert.deepEqual(declarationVerdictDisplay("proxy", "human_confirmed"),
                   { className: "verdict-proxy", label: "proxy definition" });
});

test("verdicts: the note ends with the adjudication method, the status and the resolution", () => {
  const { index } = fixture();
  const [verdict] = index.items.get("item:theorem-1.1").verdicts;
  assert.deepEqual(verdictNote(verdict), {
    className: "verdict-note verdict-fixed",
    text: "Faithfulness: fixed: statement deviation (medium). The box bound was stated for closed boxes only. "
      + "[adjudication, fixed; fixed, PR #7]",
    title: "Notes/Box.lean:12\nsource page 2\ntwo independent readings and a reconciler",
  });
  const bare = verdictNote({ severity: "none", status: "human_confirmed", summary: "S.", verdict: "proxy" });
  assert.equal(bare.text, "Faithfulness: proxy definition. S. [human confirmed]");
  assert.equal(bare.title, "");
});

test("verdicts: a resolution adds one line, and an open verdict adds none", () => {
  assert.equal(resolutionText({ pr: 7, state: "fixed" }), "fixed, PR #7");
  assert.equal(resolutionText({ branch: "a-branch", state: "in_progress" }), "in progress, branch a-branch");
  assert.equal(resolutionText({ issue: 3, note: "by design", state: "argued_correct" }),
               "argued correct, issue #3, by design");
  assert.equal(resolutionText({ state: "open" }), "");
  assert.equal(resolutionText(undefined), "");
});

// --- the PDF's bytes --------------------------------------------------------

test("pdf: the bytes are verified against the pinned sha256", async () => {
  const good = await verifyPdfBytes(PDF_BYTES, PDF_SHA256);
  assert.equal(good.ok, true);
  const other = fakeSha256("0");
  const bad = await verifyPdfBytes(PDF_BYTES, other);
  assert.equal(bad.ok, false);
  assert.equal(bad.actual, PDF_SHA256);
  const flipped = PDF_BYTES.slice();
  flipped[0] ^= 1;
  assert.equal((await verifyPdfBytes(flipped, PDF_SHA256)).ok, false);
  assert.equal((await verifyPdfBytes(PDF_BYTES, PDF_SHA256.toUpperCase())).ok, true);
  await assert.rejects(() => verifyPdfBytes(PDF_BYTES, "not-a-digest"), /64 hex digits/);
  await assert.rejects(() => verifyPdfBytes(PDF_BYTES, PDF_SHA256, {}), /WebCrypto is unavailable/);
});

// --- the card list ----------------------------------------------------------

test("cards: filtering, paging and grouping keep the role order", () => {
  const { index } = fixture();
  const cards = cardsOf(index, "item:theorem-1.1");
  assert.equal(cardsMatching(cards, { role: "key" }).length, 1);
  assert.equal(cardsMatching(cards, { query: "volume" }).length, 2);
  assert.equal(cardsMatching(cards, { query: "Notes.Set" }).length, 3);
  assert.equal(cardsMatching(cards, { query: "nothing here" }).length, 0);
  assert.equal(cardsMatching(cards, {}).length, 5);
  const page = paginateCards(cards, 2, 2);
  assert.deepEqual([page.page, page.pageCount, page.total], [2, 3, 5]);
  assert.deepEqual(page.items.map((card) => card.role), ["support", "example"]);
  assert.equal(paginateCards(cards, 99, 2).page, 3);
  assert.equal(paginateCards([], 1, 2).pageCount, 1);
  assert.throws(() => paginateCards(cards, 1, 0), /positive integer/);
  assert.deepEqual(groupCards(cards).map((group) => group.role), LINK_ROLES);
  const doubled = groupCards([cards[0], { ...cards[1], role: "key" }, cards[2]]);
  assert.deepEqual(doubled.map((group) => [group.role, group.cards.length]), [["key", 2], ["support", 1]]);
});
