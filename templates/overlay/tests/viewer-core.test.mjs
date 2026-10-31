/**
 * The overlay viewer's pure half, against synthetic annotation files and configs.
 *
 * Everything the contract (docs/overlay.md section 4) says the viewer builds
 * from the Literature Links 1.0 annotations is checked here: the bundle, the
 * refusals, geometry, link ordering, one card per link however many records
 * repeat it, status and note, the href rules, verdict display and sha256
 * verification. The
 * controller tests are in viewer-controller.test.mjs.
 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import {
  COVERAGE_SUMMARIES,
  COVERAGE_VALUES,
  LINK_ROLES,
  NAMESPACE,
  VERDICT_KINDS,
  VERDICT_LABELS,
  buildIndex,
  cardHref,
  cardTarget,
  cardsMatching,
  coverageSummary,
  coverageWord,
  declarationVerdictDisplay,
  docBlocks,
  endRegion,
  firstKeyCard,
  hoverText,
  itemFolder,
  labelParts,
  leanTokens,
  linesText,
  locatorTail,
  locatorText,
  modulePage,
  pageRows,
  pageSummary,
  pageSummaryText,
  paginateCards,
  parseBundle,
  pdfSource,
  pointToViewport,
  preferredItemId,
  quadToViewport,
  readConfig,
  readAnnotationsBlock,
  readVerso,
  regionCoverage,
  regionVerdict,
  resolutionText,
  reviewFooter,
  safeBackLinkHref,
  safeHttpsUrl,
  sha256Hex,
  sharedRole,
  siteHref,
  splitCards,
  siteRootFor,
  splitName,
  stepCards,
  scriptRuns,
  stepParts,
  stepRelation,
  targetHref,
  verdictDisplay,
  verdictFooter,
  verdictLine,
  verifyPdfBytes,
  versoFragment,
  versoRuns,
} from "../runtime/viewer/viewer-core.js";
import {
  HERE,
  ORIGIN,
  OTHER_SOURCE_ID,
  PDF_BYTES,
  PDF_ROUTE,
  REPOSITORY,
  SOURCE_ID,
  VERSO_TARGET,
  VIEWER_DIR,
  fakeSha256,
  fakeViewport,
  makeConfig,
  annotationFiles,
  bundleText,
  fileInputs,
  linkRecord,
  makeGenerated,
  makeProofSections,
  makeSections,
  makeVerso,
} from "./fixtures.mjs";

const PDF_SHA256 = await sha256Hex(PDF_BYTES);

function fixture(sections = makeSections(PDF_SHA256), configOverrides = {}) {
  const config = readConfig(makeConfig(configOverrides));
  return { config, index: buildIndex(fileInputs(sections), config, { pageOrigin: ORIGIN }) };
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
  assert.equal(config.minimal, false, "an overlay explains itself unless the config says otherwise");
  assert.equal(readConfig(makeConfig({ display: { minimal: true } })).minimal, true);
  assert.equal(readConfig(makeConfig({ display: { minimal: "yes" } })).minimal, false, "only true turns it on");
  assert.equal(config.storageKey, "notes-overlay-page");
  const bare = readConfig({ pdf: { route: PDF_ROUTE }, schema: "formal-companion/overlay-config",
                            schema_version: 1, source_id: SOURCE_ID, title: "T" });
  assert.equal(bare.backLink, null, "no back_link is no back link");
  assert.deepEqual(readConfig(makeConfig()).backLink, { href: "../../index.html", text: "Back to the notes" });
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

// --- the bundle -------------------------------------------------------------

test("bundle: GENERATED.json's annotations block names it, its sha256 and the file count", async () => {
  const sections = makeSections(PDF_SHA256);
  const block = readAnnotationsBlock(await makeGenerated(sections));
  assert.equal(block.directory, "annotations");
  assert.equal(block.count, annotationFiles(sections).length);
  assert.equal(block.bundle.path, "annotations.bundle.json");
  assert.match(block.bundle.sha256, /^[a-f\d]{64}$/);
});

test("bundle: an absent block, no file or an unsafe bundle path is refused", () => {
  const bundle = { path: "annotations.bundle.json", sha256: fakeSha256("a") };
  const good = { bundle, count: 1, directory: "annotations", manifest_sha256: fakeSha256("m") };
  for (const [generated, pattern] of [
    [{}, /no annotations block/],
    [{ annotations: { ...good, directory: "../x" } }, /annotations\.directory/],
    [{ annotations: { ...good, count: 0 } }, /records no annotation file/],
    [{ annotations: { ...good, bundle: { ...bundle, path: "../x.json" } } }, /annotations\.bundle/],
    [{ annotations: { ...good, bundle: { ...bundle, sha256: "x" } } }, /annotations\.bundle/],
    [{ annotations: { ...good, bundle: undefined } }, /annotations\.bundle/],
  ]) {
    assert.throws(() => readAnnotationsBlock(generated), pattern);
  }
});

test("bundle: every annotation with its path in file order, and anything else refused", async () => {
  const sections = makeSections(PDF_SHA256);
  const block = readAnnotationsBlock(await makeGenerated(sections));
  assert.deepEqual(parseBundle(bundleText(sections), block), annotationFiles(sections));
  const files = annotationFiles(sections);
  const of = (annotations, extra = {}) => JSON.stringify({ annotations, count: annotations.length,
                                                           directory: "annotations",
                                                           schema: "formal-companion/overlay-annotations", ...extra });
  for (const [text, pattern] of [
    ["{oops", /annotations\.bundle\.json is not JSON/],
    [of(files, { schema: "x" }), /is not a formal-companion\/overlay-annotations bundle/],
    [of(files, { directory: "other" }), /bundle of annotations\//],
    [of([{ ...files[0], path: "annotations/1.jsonl" }]), /annotations\[0\]/],
    [of([{ ...files[0], path: "annotations/1/x/y/z.json" }]), /annotations\[0\]/],
    [of([{ ...files[0], path: "annotations/1/x/_defaults.json" }]), /annotations\[0\]/],
    [of([{ ...files[0], path: "annotations/_defaults.json" }]), /annotations\[0\]/],
    [of([{ ...files[0], path: "annotations/../1/x.json" }]), /annotations\[0\]/],
    [of([{ ...files[0], path: "annotations/1/.x.json" }]), /annotations\[0\]/],
    [of([{ ...files[0], annotation: [1] }]), /annotations\[0\]/],
    [of(files.slice(1)), /holds \d+ annotations; GENERATED\.json records \d+/],
  ]) {
    assert.throws(() => parseBundle(text, block), pattern);
  }
  const inItem = [{ ...files[0], path: "annotations/1.1/theorem-1.1/key__A.json" }];
  assert.deepEqual(parseBundle(of(inItem), { ...block, count: 1 }), inItem, "a record in its item's folder");
});

// --- the source -------------------------------------------------------------

test("source: the PDF is the records' subject ends the config names, one sha256 for all", () => {
  const files = fileInputs(makeSections(PDF_SHA256));
  assert.equal(pdfSource(files, SOURCE_ID).sha256, PDF_SHA256);
  assert.equal(pdfSource(files, OTHER_SOURCE_ID).sha256, fakeSha256("e"));
  assert.throws(() => pdfSource(files, "src:nowhere"), /No annotation is a record of the PDF "src:nowhere"/);
  const split = makeSections(PDF_SHA256);
  split[2][0].subject[0] = { ...split[2][0].subject[0], sha256: fakeSha256("1") };
  assert.throws(() => pdfSource(fileInputs(split), SOURCE_ID), /two different PDFs/);
  const unpinned = makeSections("not a digest");
  assert.throws(() => pdfSource(fileInputs(unpinned), SOURCE_ID), /states no sha256/);
});

// --- geometry ---------------------------------------------------------------

test("geometry: a rect is [x, y, width, height] from the top left", () => {
  assert.deepEqual(endRegion({ location: { page: 2, rect: [0.1, 0.2, 0.5, 0.25] } }), {
    page: 2,
    quads: [[[0.1, 0.2], [0.6, 0.2], [0.6, 0.45], [0.1, 0.45]]],
  });
});

test("geometry: the extension's quads replace the bounding rect", () => {
  const end = { extensions: { [NAMESPACE]: { quads: [[0.1, 0.2, 0.9, 0.2, 0.9, 0.3, 0.1, 0.3]] } },
                location: { page: 3, rect: [0.1, 0.2, 0.8, 0.1] } };
  assert.deepEqual(endRegion(end).quads, [[[0.1, 0.2], [0.9, 0.2], [0.9, 0.3], [0.1, 0.3]]]);
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
    assert.throws(() => endRegion({ location }), pattern);
  }
  const bad = { extensions: { [NAMESPACE]: { quads: [[0, 0, 1]] } }, location: { page: 1, rect: [0, 0, 1, 1] } };
  assert.throws(() => endRegion(bad), /malformed quad/);
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

test("index: a region lists its items in the curation's order, not the files'", () => {
  const { index } = fixture();
  const [region] = index.pages.get(7);
  assert.deepEqual(region.itemIds, ["item:remark-7.1", "item:equation-7"],
                   "region_items names the remark first; the equation's file comes first");
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

test("index: the regions on a page are in reading order, whatever order the files came in", () => {
  const sections = makeSections(PDF_SHA256);
  const files = fileInputs(sections);
  const config = readConfig(makeConfig());
  const forward = buildIndex(files, config, { pageOrigin: ORIGIN });
  const backward = buildIndex([...files].reverse(), config, { pageOrigin: ORIGIN });
  for (const index of [forward, backward]) {
    assert.deepEqual(index.pages.get(2).map((region) => region.id), ["loc:0001", "loc:0004"]);
  }
  sections[1][0].subject[0].location = { page: 2, rect: [0.8, 0.4, 0.1, 0.1] };
  const beside = buildIndex(fileInputs(sections), config, { pageOrigin: ORIGIN });
  assert.deepEqual(beside.pages.get(2).map((region) => region.id), ["loc:0004", "loc:0001"],
                   "on one line, left before right: the region sits where its first end put it");
});

test("index: the cards of one role are in declaration order, whatever order the files came in", () => {
  const sections = makeSections(PDF_SHA256);
  const theorem = linkRecord(sections, "item:theorem-1.1", "key", "Notes.Box.volume_nonneg");
  const second = JSON.parse(JSON.stringify(theorem));
  second.object[0].location.declaration = "Notes.Box.a_first";
  second.object[0].extensions[NAMESPACE].declaration.id = "Notes.Box.a_first";
  sections[2].push(second);
  const config = readConfig(makeConfig());
  for (const files of [fileInputs(sections), fileInputs(sections).reverse()]) {
    const keys = buildIndex(files, config, { pageOrigin: ORIGIN }).items.get("item:theorem-1.1").cards
      .filter((card) => card.role === "key").map((card) => card.id);
    assert.deepEqual(keys, ["item:theorem-1.1/key/Notes.Box.a_first", "item:theorem-1.1/key/Notes.Box.volume_nonneg"]);
  }
});

test("index: a region that cannot be placed is dropped with a warning, not guessed", () => {
  const sections = makeSections(PDF_SHA256);
  sections[2][0].subject[0].location = { page: 5, rect: [0.9, 0.1, 0.4, 0.1] };
  sections[1][5].subject[0].location = { page: 40, rect: [0.1, 0.1, 0.1, 0.1] };
  const { index } = fixture(sections);
  assert.equal(index.warnings.length, 2);
  assert.match(index.warnings.join("\n"), /item:exercise-3\.1: .*off the page/);
  assert.match(index.warnings.join("\n"), /page 40 is past the source's 12 pages/);
  assert.equal(index.pages.has(5), false);
  assert.equal(index.items.has("item:definition-2.1"), false, "a record with no region left adds nothing");
  const one = makeSections(PDF_SHA256);
  linkRecord(one, "item:theorem-1.1", "key", "Notes.Box.volume_nonneg").subject[0].location.page = 40;
  const kept = fixture(one).index;
  assert.equal(kept.warnings.length, 1);
  assert.equal(cardsOf(kept, "item:theorem-1.1").length, 5, "the record's other region still places it");
});

test("index: one card per link, ordered key, attributed, support, example, helper", () => {
  const { index } = fixture();
  const cards = cardsOf(index, "item:theorem-1.1");
  assert.deepEqual(cards.map((card) => card.role), LINK_ROLES,
                   "a second record repeats the key link, and the item still has five cards");
  assert.deepEqual(index.regions.find((region) => region.id === "loc:0004").itemIds,
                   ["item:remark-4.1", "item:theorem-1.1"], "each subject end adds its region");
});

test("index: a checked absence adds no card", () => {
  const sections = makeSections(PDF_SHA256);
  Object.assign(sections[2][0], { object: [{ type: "lean", uri: "https://git.example.invalid/r", version: "1".repeat(40) }],
                                  relation: "noMatch", status: "not_formalized" });
  const exercise = fixture(sections).index.items.get("item:exercise-3.1");
  assert.deepEqual([exercise.cards, exercise.coverage], [[], "not_formalized"]);
});

test("index: a record's status is the coverage when its extension names no class", () => {
  const sections = makeSections(PDF_SHA256);
  const [definition] = sections[1].filter((record) => record.id === "item:definition-2.1");
  delete definition.extensions[NAMESPACE].coverage;
  assert.equal(fixture(sections).index.items.get("item:definition-2.1").coverage, "not_formalized");
  definition.status = "support_only";
  assert.equal(fixture(sections).index.items.get("item:definition-2.1").coverage, "support_only");
});

test("index: a card with no declaration in its location is named by the end's title", () => {
  const sections = makeSections(PDF_SHA256);
  const [definition] = sections[1].filter((record) => record.id === "item:definition-2.1");
  const [end] = definition.object;
  end.location = { lines: [40], module: "Notes.Set" };
  end.extensions[NAMESPACE].declaration = { id: "01.instance@Notes/Set.lean:40", kind: "instance" };
  end.title = "instance@Notes/Set.lean:40";
  const [card] = cardsOf(fixture(sections).index, "item:definition-2.1");
  assert.equal(card.fullName, "instance@Notes/Set.lean:40");
  assert.equal(card.declarationId, "01.instance@Notes/Set.lean:40", "the id still orders the card");
  delete end.title;
  assert.equal(cardsOf(fixture(sections).index, "item:definition-2.1")[0].fullName, "01.instance@Notes/Set.lean:40");
});

test("index: a card carries its snippet, link, badges and declaration verdict", () => {
  const { index } = fixture();
  const [key, attributed, support, example, helper] = cardsOf(index, "item:theorem-1.1");
  assert.equal(key.name, "Box.volume_nonneg");
  assert.equal(key.fullName, "Notes.Box.volume_nonneg");
  assert.equal(key.module, "Notes.Box");
  assert.equal(key.href, `${ORIGIN}/Notes/Box/index.html#Notes___Box___volume_nonneg`,
               "the module's page under the site root, at the declaration's anchor");
  assert.deepEqual(key.snippet, { line: 12, text: "theorem volume_nonneg (b : Box) : 0 <= b.volume" });
  assert.deepEqual(key.verdict, { note: "repaired in place", status: "fixed", verdict: "faithful" });
  assert.deepEqual(key.badges, []);
  assert.equal(attributed.href, `${ORIGIN}/Notes/Box/index.html`);
  assert.deepEqual(attributed.badges, [], "a fallback is said by the source link, not a badge");
  assert.equal(attributed.resolution, "fallback");
  assert.equal(attributed.fallbackLabel, "module page (fallback)");
  assert.equal(support.href, "https://notes.example.invalid/api/Notes/Set.html#Notes___Set___inter_sub");
  assert.equal(example.href, null, "a declaration with no rendered page is not a link");
  assert.equal(example.module, "Notes.Set");
  assert.deepEqual(example.badges, ["anonymous"], "a missing link is said by the source link, not a badge");
  assert.equal(example.fullName, "example:0123456789ab");
  assert.deepEqual(helper.badges, ["sorry: exercise"]);
  const [aux] = cardsOf(index, "item:remark-4.1");
  assert.deepEqual(aux.badges, ["private"]);
  assert.equal(aux.declarationId, "private:Notes.Internal:Notes.Internal.aux");
});

test("index: an end from elsewhere, with no overlay extension, still shows as a link", () => {
  const { index } = fixture();
  const item = index.items.get("plain-link");
  assert.equal(item.label, "Lemma 6.1");
  assert.equal(item.cards[0].href, `${ORIGIN}/Notes/Plain/index.html`);
  assert.equal(index.pages.get(6)[0].id, "p6:0.2,0.2,0.2,0.2");
});

test("index: status, note, verdicts, locator and clusters ride on the item", () => {
  const { index } = fixture();
  const definition = index.items.get("item:definition-2.1");
  assert.equal(definition.coverage, "support_only", "the extension's class, which its status does not name");
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
  const absent = makeSections(PDF_SHA256);
  absent[2][0].status = "not_formalized";
  assert.equal(fixture(absent).index.items.get("item:exercise-3.1").coverage, "not_formalized");
});

test("locator: the cluster locator, else the printed and physical pages", () => {
  assert.equal(locatorText({ physical: [21, 25], printed: [5, 9], text: "Section 1.1, Definition 1.1.1" }),
               "Section 1.1, Definition 1.1.1");
  assert.equal(locatorText({ physical: [21, 25], printed: [5, 9] }), "printed pp. 5, 9, PDF pp. 21, 25");
  assert.equal(locatorText({ physical: [3] }), "PDF p. 3");
  assert.equal(locatorText({ text: "printed pp. 46--49: Exercises 1.6.7--1.6.9 -- see p. 3" }),
               "printed pp. 46–49: Exercises 1.6.7–1.6.9 -- see p. 3");
  assert.equal(locatorText(undefined), "");
});

test("provenance: each card carries its link's method and whether a person reviewed it", () => {
  const { index } = fixture();
  const theorem = index.items.get("item:theorem-1.1");
  assert.deepEqual(theorem.cards.map((card) => [card.provenanceMethod, card.reviewed]), [
    ["curation", false], ["curation", false], ["curation", false], ["pipeline", true], ["curation", false],
  ]);
  const [definition] = index.items.get("item:definition-2.1").cards;
  assert.deepEqual([definition.provenanceMethod, definition.reviewed], ["", null]);
});

test("footer: one line says how the links were made and whether anyone reviewed them", () => {
  const { index } = fixture();
  const theorem = index.items.get("item:theorem-1.1");
  assert.equal(reviewFooter(theorem), "Curation and pipeline links, partly reviewed.");
  assert.equal(reviewFooter(theorem, { faithfulness: true }),
               "Curation and pipeline links and machine verdicts, partly reviewed.");
  assert.equal(reviewFooter(index.items.get("item:definition-2.1")), "", "a bare \"Links.\" says nothing");
  assert.equal(reviewFooter(index.items.get("item:exercise-3.1"), { faithfulness: true }), "");
  const curated = { cards: [{ provenanceMethod: "curation", reviewed: false }], verdicts: [] };
  assert.equal(reviewFooter(curated), "Curation links, unreviewed.");
  const machine = { cards: [{ provenanceMethod: "", reviewed: false }],
                    verdicts: [{ status: "unconfirmed", verdict: "faithful" }] };
  assert.equal(reviewFooter(machine, { faithfulness: true }), "Links and machine verdicts, unreviewed.");
  assert.equal(reviewFooter(machine), "Links, unreviewed.");
  const reviewed = { cards: [{ provenanceMethod: "curation", reviewed: true }],
                     verdicts: [{ status: "human_confirmed", verdict: "proxy" }] };
  assert.equal(reviewFooter(reviewed, { faithfulness: true }), "Curation links and machine verdicts, reviewed.");
  assert.equal(reviewFooter({ cards: [], verdicts: [{ status: "unconfirmed", verdict: "proxy" }] },
                            { faithfulness: true }), "Machine verdicts, unreviewed.");
  assert.equal(verdictFooter([machine, curated]), "Machine verdicts, unreviewed.");
  assert.equal(verdictFooter([curated]), "");
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

test("coverage: a summary describes the item, not whose Lean it is or how far it falls short", () => {
  for (const value of COVERAGE_VALUES) {
    assert.doesNotMatch(COVERAGE_SUMMARIES[value], /this companion|special case/i, `${value}'s summary`);
  }
  assert.equal(coverageSummary("formalized"), "Formalized: a key declaration states it.");
  assert.equal(coverageSummary("partial"),
               "Formalized in part or in a different form; see the faithfulness verdict, if any.");
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
  assert.match(css, /\.visually-hidden \{[^}]*clip-path: inset\(50%\)/, "a minimal overlay's chip is clipped out of sight");
  assert.match(css, /\.status-chip\.visually-hidden:focus-within \{[^}]*clip-path: none/,
               "and comes back while it holds keyboard focus");
  assert.ok(css.includes(".verdict-badge.verdict-fixed"));
});

test("href: a target resolves against its own file and must be https or this site", () => {
  const file = `${ORIGIN}/annotations/1.1/theorem-1.1.json`;
  assert.equal(targetHref("../../A/index.html#x", file, ORIGIN), `${ORIGIN}/A/index.html#x`);
  assert.equal(targetHref({ uri: "B.html" }, file, ORIGIN), `${ORIGIN}/annotations/1.1/B.html`);
  assert.equal(targetHref("../A.html", file, ORIGIN), `${ORIGIN}/annotations/A.html`,
               "one ../ short stays inside annotations/");
  assert.equal(targetHref("../../../A/index.html#x", `${ORIGIN}/annotations/1.1/theorem-1.1/key__A.json`, ORIGIN),
               `${ORIGIN}/A/index.html#x`, "from an item folder, one ../ more");
  assert.equal(targetHref("https://docs.example.invalid/A.html#a%22b", file, ORIGIN),
               "https://docs.example.invalid/A.html#a%22b");
  for (const bad of ["javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,x",
                     "http://elsewhere.example.invalid/x", "//elsewhere.example.invalid/x",
                     "https://user:pw@docs.example.invalid/", "a b.html", "a\\b.html", "", null, 7, {}]) {
    assert.equal(targetHref(bad, file, ORIGIN), null, `${JSON.stringify(bad)} must not be a link`);
  }
  // A plain-http site is this site only when the page is served from it.
  assert.equal(targetHref("../../A.html", file, "http://other.invalid:1"), null);
  assert.equal(safeHttpsUrl("https://a.invalid/x"), "https://a.invalid/x");
  assert.equal(safeHttpsUrl("https://a.invalid/x#y"), null);
});

test("href: a card's page is the module's under the site root, at the declaration's anchor", () => {
  assert.equal(modulePage("Notes.Box"), "Notes/Box/index.html");
  assert.equal(versoFragment("Notes.Box.mk«x»"), "Notes___Box___mk«x»");
  const site = `${ORIGIN}/`;
  const end = (destination, location = { declaration: "Notes.Box.mk«x»", module: "Notes.Box" }) => (
    { extensions: { [NAMESPACE]: { destination } }, location });
  assert.equal(cardHref(end({ resolution: "exact" }), site, ORIGIN),
               `${ORIGIN}/Notes/Box/index.html#Notes___Box___mk%C2%ABx%C2%BB`);
  assert.equal(cardHref(end({ resolution: "fallback", fallback_label: "module page" }), site, ORIGIN),
               `${ORIGIN}/Notes/Box/index.html`);
  assert.equal(cardHref(end({ href: "Other/index.html", fragment: "a b", resolution: "exact" }), site, ORIGIN),
               `${ORIGIN}/Other/index.html#a%20b`);
  assert.equal(cardHref(end({ href: "https://docs.example.invalid/B.html", resolution: "exact" }), site, ORIGIN),
               "https://docs.example.invalid/B.html#Notes___Box___mk%C2%ABx%C2%BB");
  assert.equal(cardHref(end({ resolution: "exact" }, { lines: [3], module: "Notes.Box" }), site, ORIGIN),
               `${ORIGIN}/Notes/Box/index.html`, "an anonymous declaration has no anchor of its own");
  assert.equal(cardHref(end(undefined), site, ORIGIN), null, "no destination, no page");
  assert.equal(cardHref(end({ resolution: "exact" }, { declaration: "X" }), site, ORIGIN), null, "no module, no page");
  assert.equal(cardHref(end({ href: "javascript:alert(1)", resolution: "fallback" }), site, ORIGIN), null);
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
  assert.equal(evil.backLink, null, "a refused href is no link, not a guessed one");
  assert.equal(readConfig(makeConfig({ back_link: { href: "../../index.html", text: " " } })).backLink, null);
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

test("verdicts: a line is the kind, the summary and the resolution, never 'unconfirmed'", () => {
  const { index } = fixture();
  const [verdict] = index.items.get("item:theorem-1.1").verdicts;
  assert.deepEqual(verdictLine(verdict), {
    className: "verdict-fixed",
    label: "fixed: statement deviation (medium)",
    resolution: "PR #7",
    statusWord: "",
    summary: "The box bound was stated for closed boxes only.",
    title: "Notes/Box.lean:12\nsource page 2\ntwo independent readings and a reconciler\nadjudication",
  });
  const open = verdictLine({ severity: "none", status: "unconfirmed", summary: "S.", verdict: "faithful" });
  assert.deepEqual([open.label, open.statusWord, open.title], ["faithful", "", ""]);
  assert.equal(verdictLine({ resolution: { state: "fixed" }, status: "fixed", verdict: "proxy" }).resolution, "",
               "a fixed verdict does not say fixed twice");
  assert.equal(verdictLine({ resolution: { pr: 2, state: "fixed" }, status: "unconfirmed", verdict: "proxy" }).resolution,
               "fixed, PR #2");
  assert.equal(verdictLine({ status: "human_confirmed", summary: "S.", verdict: "proxy" }).statusWord,
               "human confirmed");
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

test("cards: filtering and paging keep the role order", () => {
  const { index } = fixture();
  const cards = cardsOf(index, "item:theorem-1.1");
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
});

// --- the bridge view --------------------------------------------------------

test("labels: a trailing parenthetical that names something becomes the subtitle", () => {
  assert.deepEqual(labelParts("Definition 2.1 (Boxes, unions, volumes)"),
                   { subtitle: "Boxes, unions, volumes", title: "Definition 2.1" });
  assert.deepEqual(labelParts("Theorem 2 (Main estimate)"), { subtitle: "Main estimate", title: "Theorem 2" });
  for (const numbered of ["Equation (1.1)", "Equation (A.1)", "Equation (B.12)", "Equation (3.2b)", "Theorem 2 (ii)",
                          "Exercise 4 (c)"]) {
    assert.deepEqual(labelParts(numbered), { subtitle: "", title: numbered }, `${numbered} numbers, it does not name`);
  }
  assert.deepEqual(labelParts("Remark 7.1 and equation (7)"), { subtitle: "", title: "Remark 7.1 and equation (7)" });
  assert.deepEqual(labelParts("Théorème 2 (Énoncé)"), { subtitle: "Énoncé", title: "Théorème 2" });
  assert.deepEqual(labelParts(""), { subtitle: "", title: "" });
});

test("labels: the locator drops the segment that repeats the label, and only that", () => {
  assert.equal(locatorTail("Display 4.2", "Display 4.2, p. 7, in section 2.1"), "p. 7, in section 2.1");
  assert.equal(locatorTail("Lemma 5", "Part 2, Lemma 5, p. 11"), "Part 2, p. 11");
  const long = "Part 3, Lemma 3.4 and Exercises 3.4.1 and 3.4.6, printed pp. 12 and 14, PDF pp. 30 and 32.";
  assert.equal(locatorTail("Lemma 3.4 (Unions of boxes)", long), long, "nothing repeats the label");
  assert.equal(locatorTail("Lemma 2", "Lemma 2"), "");
  assert.equal(locatorTail("Lemma 2", ""), "");
});

test("labels: the locator stops where a second locator starts", () => {
  assert.equal(locatorTail("Exercise 6.1", "Part 6.2; second reading: Lemma 6.3 and Exercise 6.1"), "Part 6.2");
  assert.equal(locatorTail("Exercise 6.1", "Part 6.2, Exercise 6.1 / Part 6.4 slice, p. 40"), "Part 6.2");
  assert.equal(locatorTail("Exercise 6.1", "; only a second"), "; only a second", "nothing before the cut keeps it all");
});

test("names: a declaration splits into its namespace and its leaf", () => {
  assert.deepEqual(splitName("Notes.Box.volume_nonneg"), ["Notes.Box.", "volume_nonneg"]);
  assert.deepEqual(splitName("theorem_2_4"), ["", "theorem_2_4"]);
  assert.deepEqual(splitName("example:0123456789ab"), ["", "example:0123456789ab"]);
  assert.deepEqual(splitName(undefined), ["", ""]);
});

test("coverage: one word per status for the pill, and none for an unknown one", () => {
  for (const value of COVERAGE_VALUES) {
    assert.ok(coverageWord(value), `${value} has no word`);
  }
  assert.equal(coverageWord("formalized"), "Formalized");
  assert.equal(coverageWord("example_only"), "Examples only");
  assert.equal(coverageWord("mostly"), "");
});

test("cards and regions: key cards apart from the rest, and coverage picks the stroke", () => {
  const { index } = fixture();
  const theorem = index.items.get("item:theorem-1.1");
  assert.equal(firstKeyCard(theorem).fullName, "Notes.Box.volume_nonneg");
  assert.equal(firstKeyCard(index.items.get("item:definition-2.1")), null);
  const { keys, related } = splitCards(theorem);
  assert.deepEqual(keys.map((card) => card.fullName), ["Notes.Box.volume_nonneg"]);
  assert.deepEqual(related.map((card) => card.role), ["attributed", "support", "example", "helper"]);
  assert.deepEqual(splitCards(null), { keys: [], related: [] });
  assert.equal(sharedRole(related), "", "no role is more than half");
  assert.equal(sharedRole([{ role: "support" }, { role: "support" }, { role: "helper" }]), "support");
  assert.equal(sharedRole([{ role: "support" }, { role: "helper" }]), "");
  assert.equal(sharedRole([]), "");
  assert.equal(regionCoverage(theorem), "formalized");
  assert.equal(regionCoverage(index.items.get("item:definition-2.1")), "partial");
  assert.equal(regionCoverage(index.items.get("item:exercise-3.1")), "quiet");
  assert.equal(regionCoverage(index.items.get("plain-link")), "quiet", "no status and no key card");
  assert.equal(regionCoverage({ cards: [{ role: "key" }], coverage: "" }), "formalized");
  assert.equal(regionCoverage({ cards: [{ role: "key" }], coverage: "not_formalized" }), "quiet");
});

test("regions: the stroke shows the most serious open verdict, else fixed, else none", () => {
  const item = (...verdicts) => ({ cards: [], verdicts });
  assert.equal(regionVerdict([item({ status: "unconfirmed", verdict: "faithful" }),
                              item({ status: "unconfirmed", verdict: "proxy" })]), "proxy");
  assert.equal(regionVerdict([item({ status: "unconfirmed", verdict: "convention" },
                                   { status: "contested", verdict: "deviation" })]), "deviation");
  assert.equal(regionVerdict([item({ status: "fixed", verdict: "deviation" })]), "fixed");
  assert.equal(regionVerdict([item()]), null);
  assert.equal(regionVerdict([{ cards: [{ verdict: { status: "unconfirmed", verdict: "attribution_gap" } }],
                                verdicts: [] }]), "attribution_gap", "cards speak when the item has none");
});

test("page: the count line counts passages and those a key declaration states", () => {
  const { index } = fixture();
  const items = ["item:theorem-1.1", "item:definition-2.1", "item:exercise-3.1"].map((id) => index.items.get(id));
  assert.deepEqual(pageSummary(items), { passages: 3, withKey: 1 });
  assert.equal(pageSummaryText(items), "3 passages · 1 with a key declaration");
  assert.equal(pageSummaryText(items.slice(0, 1)), "1 passage · 1 with a key declaration");
  assert.equal(pageSummaryText([]), "No highlighted passages on this page.");
});

test("lean: the tint runs join back to the snippet exactly", () => {
  const samples = [
    "theorem volume_nonneg (b : Box) : 0 <= b.volume",
    "abbrev BoxSet (d : ℕ) :=\n  {s : Set (Fin d → ℝ) // IsBoxUnion s}",
    "structure Box (d : ℕ) where\n  /-- The sides of the box. -/\n  sides : Fin d → Interval",
    "… volume (b.shift v) = volume b -- trailing\nfun x => by simp",
    "noncomputable instance : Inhabited Box := ⟨default⟩\nexample : True := trivial",
    "/- unterminated",
    "",
    "deftheorem by.x x.by by' by_ Typeα Type* Prop→Sort",
  ];
  for (const sample of samples) {
    assert.equal(leanTokens(sample).map(([, part]) => part).join(""), sample);
  }
  assert.deepEqual(leanTokens("theorem t : Prop := by simp"),
                   [["kw", "theorem"], ["tx", " t : "], ["kw", "Prop"], ["tx", " := "], ["kw", "by"], ["tx", " simp"]]);
  assert.deepEqual(leanTokens("a -- note\nb"), [["tx", "a "], ["cm", "-- note"], ["tx", "\nb"]]);
  assert.deepEqual(leanTokens("x /-- doc -/ y"), [["tx", "x "], ["cm", "/-- doc -/"], ["tx", " y"]]);
  assert.deepEqual(leanTokens("… f"), [["el", "…"], ["tx", " f"]]);
  const words = leanTokens("deftheorem by.x x.by by' by_ Typeα Type* Prop→Sort");
  assert.deepEqual(words.filter(([kind]) => kind === "kw").map(([, part]) => part), ["Type", "Prop", "Sort"]);
  const heads = leanTokens("private noncomputable def f\nexample : True\nprotected axiom a\nexamples");
  assert.deepEqual(heads.filter(([kind]) => kind === "kw").map(([, part]) => part),
                   ["private", "noncomputable", "def", "example", "protected", "axiom"]);
});

// --- Verso statements and hovers ----------------------------------------------

test("verso: a card's target is its page and anchor, as cardHref builds them and the extractor keys them", async () => {
  const table = JSON.parse(await readFile(path.join(HERE, "card-targets.json"), "utf8"));
  assert.equal(table.namespace, NAMESPACE);
  for (const { declaration, destination, location, target } of table.cases) {
    const end = { extensions: { [NAMESPACE]: { ...(destination ? { destination } : {}), ...(declaration ? { declaration } : {}) } },
                  location };
    assert.equal(cardTarget(end), target, JSON.stringify({ declaration, destination, location }));
    if (target && destination.resolution === "exact") {
      const [page, fragment] = target.split("#");
      assert.equal(cardHref(end, `${ORIGIN}/`, ORIGIN), new URL(`${page}#${encodeURIComponent(fragment)}`, `${ORIGIN}/`).href);
    }
  }
  const { index } = fixture();
  const [key] = cardsOf(index, "item:theorem-1.1");
  assert.equal(key.target, VERSO_TARGET);
  const plain = cardsOf(index, "plain-link")[0];
  assert.equal(plain.target, null, "an end from elsewhere has no statement in verso.json");
});

test("verso: the file is read run by run, and a statement with a run that does not read is dropped", () => {
  assert.equal(readVerso(null), null);
  assert.equal(readVerso({ ...makeVerso(), schema: "other" }), null);
  assert.equal(readVerso({ ...makeVerso(), schema_version: 2 }), null);
  const raw = makeVerso();
  raw.hovers["9"] = { signature: "" };
  raw.hovers.x = { signature: "not an id" };
  raw.statements["Other/index.html#a"] = [[["kw", "def"], ["tx", 7]], [["weird", "def"], ["tx", " f", 99, -1, ""]]];
  raw.statements["Other/index.html#b"] = [[]];
  raw.hovers["0"] = { doc: "Hover ids start at zero." };
  raw.statements["Other/index.html#c"] = [[["kw", "by", 0]]];
  const verso = readVerso(raw);
  assert.deepEqual([...verso.hovers.keys()].sort(), ["0", "1", "2", "3", "4"]);
  assert.deepEqual(verso.statements.get("Other/index.html#c"), [[["kw", "by", "0", null, null]]]);
  assert.deepEqual(verso.statements.get("Other/index.html#a"), [[["tx", "def", null, null, null], ["tx", " f", null, null, null]]],
                   "an unknown kind is plain text; an unknown hover, a bad binding and an empty href are none");
  assert.equal(verso.statements.has("Other/index.html#b"), false);
  const [runs] = verso.statements.get(VERSO_TARGET);
  assert.deepEqual(runs[5], ["var", "b", "2", 1, null]);
  assert.deepEqual(runs[7], ["const", "Box", "3", null, "Notes/Box/index.html#Notes___Box"]);
});

test("verso: a statement is used only when its runs join to the card's snippet, exactly", () => {
  const verso = readVerso(makeVerso());
  const snippet = "theorem volume_nonneg (b : Box) : 0 <= b.volume";
  const card = { snippet: { text: snippet }, target: VERSO_TARGET };
  assert.equal(versoRuns(verso, card).map(([, part]) => part).join(""), snippet);
  assert.equal(versoRuns(verso, { ...card, snippet: { text: `${snippet} ` } }), null, "one more space is another text");
  assert.equal(versoRuns(verso, { ...card, target: "Notes/Box/index.html#other" }), null);
  assert.equal(versoRuns(verso, { ...card, target: null }), null);
  assert.equal(versoRuns(null, card), null, "no verso.json, no runs");
});

test("verso: a hover is the entry's signature, a variable's as name and type, and its docstring as blocks", () => {
  const verso = readVerso(makeVerso());
  const [runs] = verso.statements.get(VERSO_TARGET);
  assert.deepEqual(hoverText(verso, runs[2]), { doc: "The volume of a box is never negative.",
                                                signature: "Notes.Box.volume_nonneg (b : Box) : 0 <= b.volume" });
  assert.deepEqual(hoverText(verso, runs[5]), { doc: "", signature: "b : Box" });
  assert.equal(hoverText(verso, runs[0]), null, "a run with no hover has none");
  assert.deepEqual(docBlocks("A box: a product of `n` intervals,\nclosed.\n\n* one\n* two ``a`b``\n\n```lean\nexample : True := trivial\n```"), [
    { code: false, parts: [["tx", "A box: a product of "], ["code", "n"], ["tx", " intervals, closed."]] },
    { code: false, parts: [["tx", "* one\n* two "], ["code", "a`b"]] },
    { code: true, parts: [["code", "example : True := trivial"]] },
  ]);
  assert.deepEqual(docBlocks("an `unclosed tick"), [{ code: false, parts: [["tx", "an `unclosed tick"]] }]);
  assert.deepEqual(docBlocks(""), []);
});

test("verso: a token links only a page of this site", () => {
  const site = `${ORIGIN}/`;
  assert.equal(siteHref("Notes/Box/index.html#Notes___Box", site, ORIGIN), `${ORIGIN}/Notes/Box/index.html#Notes___Box`);
  for (const elsewhere of ["https://elsewhere.invalid/x.html", "javascript:alert(1)", "//elsewhere.invalid/x"]) {
    assert.equal(siteHref(elsewhere, site, ORIGIN), null, elsewhere);
  }
  // A project site shares its origin with every other site of its owner: a page of this one is under its root.
  const project = `${ORIGIN}/notes/`;
  assert.equal(siteHref("Notes/Box/index.html", project, ORIGIN), `${ORIGIN}/notes/Notes/Box/index.html`);
  for (const elsewhere of ["../other/x.html", "/other/x.html", `${ORIGIN}/other/x.html`, "../notes-other/x.html",
                           "%2e%2e/other/x.html", "Notes/../../other/x.html"]) {
    assert.equal(siteHref(elsewhere, project, ORIGIN), null, elsewhere);
  }
});

// --- proof steps --------------------------------------------------------------

const PROOF = makeProofSections(PDF_SHA256);

function proofIndex(sections = PROOF, options = { pageOrigin: ORIGIN, siteUrl: `${ORIGIN}/` }) {
  return buildIndex(fileInputs(sections), readConfig(makeConfig()), options);
}

test("steps: a step belongs to the one other item with records in its folder, whatever its id spells", () => {
  const { items } = proofIndex();
  assert.deepEqual(items.get("thm").steps, ["a", "b", "c", "d"]);
  assert.deepEqual(["a", "b", "c", "d"].map((id) => items.get(id).stepOf), Array(4).fill("thm"));
  assert.equal(items.get("thm").stepOf, null);
  assert.deepEqual(items.get("remark").steps, []);
});

test("steps: a proof's steps are in the natural order of their file names", () => {
  const [theorem, first] = PROOF["9/theorem-9.1"];
  const file = (name, record) => ({ folder: "9/t", path: `annotations/9/t/${name}.json`, records: [record],
                                    url: `${ORIGIN}/annotations/9/t/${name}.json` });
  const { items } = buildIndex([
    file("step-10", { ...first, id: "ten" }), file("key", theorem), file("step-9", { ...first, id: "nine" }),
  ], readConfig(makeConfig()));
  assert.deepEqual(items.get("thm").steps, ["nine", "ten"]);
});

test("steps: a step with no one item in its folder stays an item of its own, never guessed onto one", () => {
  const [theorem, first] = PROOF["9/theorem-9.1"];
  const other = { ...theorem, id: "other" };
  const loose = buildIndex(fileInputs({ 9: [theorem, first] }), readConfig(makeConfig()));
  const alone = buildIndex(fileInputs({ "9/t": [first] }), readConfig(makeConfig()));
  const shared = buildIndex(fileInputs({ "9/t": [theorem, other, first] }), readConfig(makeConfig()));
  for (const { items } of [loose, alone, shared]) {
    assert.equal(items.get("a").stepOf, null);
    assert.deepEqual(items.get("thm")?.steps ?? [], []);
  }
  // Its lines and its relation are still a step's.
  assert.deepEqual(stepCards(loose.items.get("a"))[0].ranges[0].lines, [20, 21]);
  assert.equal(stepRelation(loose.items.get("a")).word, "Exact match");
  assert.deepEqual(stepParts(loose.items.get("a"), loose.items),
                   { number: null, of: null, text: "", title: "a: Unfold the product" });
});

test("steps: a record of no other kind is touched: no folder, no step, the same cards in the same order", () => {
  const sections = makeSections(PDF_SHA256);
  const { items, pages } = buildIndex(fileInputs(sections), readConfig(makeConfig()));
  for (const item of items.values()) {
    assert.equal(item.stepOf, null);
    assert.deepEqual(item.steps, []);
    assert.deepEqual(item.checked, []);
  }
  assert.deepEqual(pageRows(pages.get(2), items).map((row) => [row.item.id, row.region.id, row.steps]),
                   [["item:theorem-1.1", "loc:0001", []], ["item:remark-4.1", "loc:0004", []]]);
});

test("steps: a step's ranges are grouped by declaration in the record's order, with their lines", () => {
  const { items } = proofIndex();
  const [only] = stepCards(items.get("b"));
  assert.equal(stepCards(items.get("b")).length, 1, "three ranges of one declaration are one card");
  assert.deepEqual(only.ranges.map((range) => range.lines), [[22, 23], [25, 25], [27, 40]]);
  assert.deepEqual(only.ranges.map((range) => range.snippet.line), [22, 25, 27]);
  assert.equal(only.ranges[2].snippet.truncated, true);
  assert.equal(only.ranges[0].snippet.truncated, undefined);
  assert.deepEqual(stepCards(items.get("c")).map((entry) => entry.fullName), ["Notes.Box.double", "Notes.Box.bound"]);
});

test("steps: two anonymous examples with one name and different pages stay two cards", () => {
  const example = (lines, fragment) => ({
    extensions: { [NAMESPACE]: { declaration: { anonymous: true, id: "example", kind: "example" },
                                 destination: { href: `Notes/Box/index.html#${fragment}`, resolution: "exact" },
                                 snippet: { line: lines[0], text: "example" } } },
    location: { lines, module: "Notes.Box" }, role: "key", type: "lean", uri: REPOSITORY,
  });
  const [theorem, first] = PROOF["9/theorem-9.1"];
  const { items } = buildIndex(fileInputs({ "9/t": [theorem, { ...first, object: [example([3, 3], "x"), example([9, 9], "y")] }] }),
                               readConfig(makeConfig()), { pageOrigin: ORIGIN, siteUrl: `${ORIGIN}/` });
  assert.deepEqual(stepCards(items.get("a")).map((entry) => entry.ranges[0].lines), [[3, 3], [9, 9]]);
});

test("steps: a checked absence's ends say where it looked; they are not cards, and link only through a card", () => {
  const { items } = proofIndex();
  const step = items.get("d");
  assert.deepEqual(step.cards, []);
  assert.deepEqual(step.checked.map(({ fullName, href, lines, module, name }) => ({ fullName, href, lines, module, name })), [
    { fullName: "Notes.Box.double", href: items.get("thm").cards[0].href, lines: null, module: "Notes.Box",
      name: "Box.double" },
    { fullName: "", href: null, lines: [50, 52], module: "Notes.Set", name: "Notes.Set" },
  ]);
  assert.ok(step.checked[0].href, "the theorem's card links that declaration");
  assert.equal(firstKeyCard(step), null);
  // A checked absence that is not a step keeps no ends at all.
  assert.deepEqual(items.get("item:gap").checked, []);
  assert.deepEqual(items.get("item:gap").cards, []);
});

test("steps: a step reads as its number, its proof's length and its label less its id", () => {
  const { items } = proofIndex();
  assert.deepEqual(stepParts(items.get("c"), items),
                   { number: 3, of: 4, text: "Bound each side", title: "Step 3" });
  // A label that does not repeat the id is kept whole.
  items.get("c").label = "Bound each side";
  assert.equal(stepParts(items.get("c"), items).text, "Bound each side");
  items.get("c").label = "c";
  assert.equal(stepParts(items.get("c"), items).text, "");
});

test("steps: the relation is printed; a minimal overlay leaves only an exact match unmarked", () => {
  const word = (relations, options) => stepRelation({ relations }, options).word;
  assert.equal(word(["exactMatch"]), "Exact match");
  assert.equal(word(["closeMatch"]), "Close match");
  assert.equal(word(["relatedMatch"]), "Related");
  assert.equal(word(["noMatch"]), "No match");
  assert.equal(word(["exactMatch"], { minimal: true }), "");
  assert.equal(word(["closeMatch"], { minimal: true }), "Close match");
  assert.equal(word(["relatedMatch"], { minimal: true }), "Related");
  assert.equal(word(["noMatch"], { minimal: true }), "No match");
  // Literature Links' relation is free text: an unknown token prints as written.
  assert.equal(word(["seeAlsoMatch"]), "seeAlsoMatch");
  assert.equal(word([]), "");
  assert.equal(word(["exactMatch", "closeMatch"], { minimal: true }), "Close match");
  assert.ok(stepRelation({ relations: ["closeMatch"] }).title);
  assert.equal(stepRelation({ relations: ["seeAlsoMatch"] }).title, "");
});

test("steps: a page's rows nest each step under its item, in the proof's order", () => {
  const { items, pages } = proofIndex();
  const shape = (page) => pageRows(pages.get(page), items).map((row) => [row.item.id, Boolean(row.region),
                                                                         row.steps.map((step) => step.item.id)]);
  assert.deepEqual(shape(9), [["thm", true, ["a", "b"]], ["remark", true, []]]);
  // The theorem's own region is on page 9: on page 10 it comes with its steps, with no region.
  assert.deepEqual(shape(10), [["thm", false, ["c", "d"]]]);
  assert.deepEqual(items.get("thm").place, { page: 9, regionId: pages.get(9)[0].id });
  assert.equal(items.get("c").place.page, 10);
});

test("steps: a page's count line adds its steps, and says nothing new on a page with none", () => {
  const items = [{ cards: [{ role: "key" }] }, { cards: [] }];
  assert.equal(pageSummaryText(items), "2 passages · 1 with a key declaration");
  assert.equal(pageSummaryText(items, 0), "2 passages · 1 with a key declaration");
  assert.equal(pageSummaryText(items, 1), "2 passages · 1 with a key declaration · 1 proof step");
  assert.equal(pageSummaryText([], 2), "2 proof steps");
  assert.equal(pageSummaryText([], 0), "No highlighted passages on this page.");
});

test("steps: line ranges read one way, and an item folder is the file's <section>/<item>", () => {
  assert.equal(linesText([7, 7]), "Line 7");
  assert.equal(linesText([7, 9]), "Lines 7–9");
  assert.equal(linesText(null), "");
  assert.equal(itemFolder("annotations/3E/3.101/proof-step-01.json", "annotations"), "3E/3.101");
  assert.equal(itemFolder("annotations/3E/3.90__key__x.json", "annotations"), null);
  assert.equal(itemFolder("a/b/3E/3.101/x.json", "a/b"), "3E/3.101");
});

test("titles: script characters become runs of the plain characters they stand for", () => {
  assert.deepEqual(scriptRuns("plain"), [["text", "plain"]]);
  assert.deepEqual(scriptRuns(""), []);
  assert.deepEqual(scriptRuns(undefined), []);
  assert.deepEqual(scriptRuns("fₖ ≠ 0"), [["text", "f"], ["sub", "k"], ["text", " ≠ 0"]]);
  assert.deepEqual(scriptRuns("eₘ, eₙ, eⱼ, vᵢ"), [
    ["text", "e"], ["sub", "m"], ["text", ", e"], ["sub", "n"], ["text", ", e"], ["sub", "j"], ["text", ", v"], ["sub", "i"],
  ]);
  assert.deepEqual(scriptRuns("fₖ₋₁"), [["text", "f"], ["sub", "k−1"]]);
  assert.deepEqual(scriptRuns("T⁻¹ and xⁿ⁺¹"), [["text", "T"], ["sup", "−1"], ["text", " and x"], ["sup", "n+1"]]);
  assert.deepEqual(scriptRuns("Aᵀ, tᵐ, xᵏ, wʲ, z²"), [
    ["text", "A"], ["sup", "T"], ["text", ", t"], ["sup", "m"], ["text", ", x"], ["sup", "k"], ["text", ", w"], ["sup", "j"],
    ["text", ", z"], ["sup", "2"],
  ]);
  assert.deepEqual(scriptRuns("x₍ₖ₎²"), [["text", "x"], ["sub", "(k)"], ["sup", "2"]]);
});

test("titles: other mathematics is left as written", () => {
  for (const value of ["½ × Ŝ", "⟨u, v⟩ ⟂ ℝ", "𝐅 𝑇 𝒫 ℒ", "x′", "⋯ ∑ √", "ê", "α β λ"]) {
    assert.deepEqual(scriptRuns(value), [["text", value]]);
  }
});
