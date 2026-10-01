/**
 * The overlay viewer's pure half, against synthetic documents and configs.
 *
 * Everything the contract (docs/overlay.md section 4) says the viewer
 * builds "purely from the document" is checked here: the refusals, the four
 * geometry combinations, link ordering, orphaned links, coverage, the cluster
 * fallback, the href and fragment rules, verdict display and sha256
 * verification. The controller tests are in viewer-controller.test.mjs.
 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import {
  COVERAGE_SUMMARIES,
  COVERAGE_VALUES,
  DEFAULT_BACK_LINK,
  DEFAULT_SITE_ROOT,
  DOCUMENT_SCHEMA,
  LINK_ROLES,
  VERDICT_KINDS,
  VERDICT_LABELS,
  buildIndex,
  cardSummaryText,
  cardsMatching,
  coverageSummary,
  declarationVerdictDisplay,
  destinationHref,
  encodeFragment,
  groupCards,
  normalizedPointToViewport,
  pageSizeFor,
  paginateCards,
  pdfSource,
  quadToViewport,
  readConfig,
  regionQuads,
  regionSelectors,
  resolutionText,
  safeBackLinkHref,
  safeHttpsUrl,
  safeSitePath,
  sha256Hex,
  siteRootFrom,
  verdictDisplay,
  verifyPdfBytes,
} from "../runtime/viewer/viewer-core.js";
import {
  OTHER_SOURCE_ID,
  PDF_BYTES,
  PDF_ROUTE,
  SOURCE_ID,
  VIEWER_DIR,
  fakeSha256,
  makeConfig,
  makeDocument,
} from "./fixtures.mjs";

const PDF_SHA256 = await sha256Hex(PDF_BYTES);

function fixture(documentOverrides = {}, configOverrides = {}) {
  const config = readConfig(makeConfig(configOverrides));
  const doc = makeDocument(PDF_SHA256, documentOverrides);
  return { config, doc, index: buildIndex(doc, config) };
}

function cardsOf(index, itemId) {
  return index.items.get(itemId).cards;
}

// --- the configuration ------------------------------------------------------

test("config: the viewer reads what it needs and fills section 3's defaults", () => {
  const config = readConfig(makeConfig());
  assert.equal(config.sourceId, SOURCE_ID);
  assert.equal(config.pdfRoute, PDF_ROUTE);
  assert.equal(config.title, "Elementary sets: source overlay (synthetic)");
  assert.equal(config.stripPrefix, "Notes.");
  assert.equal(config.storageKey, "notes-overlay-page");
  assert.equal(config.backLink.text, "Back to the notes");
  assert.equal(config.siteRoot, "../../");
  assert.equal(config.documentUrl, "../annotations.json");

  const bare = readConfig({
    pdf: { route: "/__pdf__/x.pdf" },
    schema: "formal-companion/overlay-config",
    schema_version: 1,
    site: { index: "index.html", mount: "pdf-overlay", output_root: "out" },
    source_id: "src:x",
    title: "A title",
  });
  assert.equal(bare.backLink.href, "../../index.html");
  assert.equal(bare.backLink.text, "Back to the site");
  assert.equal(bare.siteRoot, DEFAULT_SITE_ROOT);
  assert.equal(bare.storageKey, "formal-companion-overlay-page");
  assert.equal(bare.stripPrefix, "");
  assert.equal(bare.lede, "");
});

test("config: a wrong schema, version, missing field or unsafe route is refused", () => {
  assert.throws(() => readConfig(makeConfig({ schema: "something/else" })), /expected "formal-companion\/overlay-config"/);
  assert.throws(() => readConfig(makeConfig({ schema_version: 2 })), /expected 1/);
  assert.throws(() => readConfig(makeConfig({ source_id: "" })), /names no source_id/);
  assert.throws(() => readConfig(makeConfig({ title: "   " })), /names no title/);
  assert.throws(() => readConfig(makeConfig({ pdf: { route: "/files/x.pdf" } })), /pdf\.route/);
  assert.throws(() => readConfig(makeConfig({ pdf: { route: "/__a/../b.pdf" } })), /pdf\.route/);
  assert.throws(() => readConfig([]), /must be a JSON object/);
});

// --- the document's three refusals -----------------------------------------

test("document: the configured pdf source is found, and a 1.x minor still reads", () => {
  const doc = makeDocument(PDF_SHA256);
  const source = pdfSource(doc, SOURCE_ID);
  assert.equal(source.physical_page_count, 12);
  assert.equal(source.sha256, PDF_SHA256);
  assert.equal(pdfSource({ ...doc, schema_version: "1.7" }, SOURCE_ID).id, SOURCE_ID);
});

test("document: a wrong schema, a wrong major version or a missing source is refused", () => {
  const doc = makeDocument(PDF_SHA256);
  assert.equal(DOCUMENT_SCHEMA, "lean-companion/annotations");
  assert.throws(() => pdfSource({ ...doc, schema: "notes/overlay" }, SOURCE_ID), /has schema/);
  // The vendored format pins one schema id, so a neighbouring spelling of the
  // same format is refused rather than translated.
  assert.throws(
    () => pdfSource({ ...doc, schema: "formal-companion/annotations" }, SOURCE_ID),
    /has schema "formal-companion\/annotations"/,
  );
  assert.throws(() => pdfSource({ ...doc, schema_version: "2.0" }, SOURCE_ID), /schema_version/);
  assert.throws(() => pdfSource({ ...doc, schema_version: 1 }, SOURCE_ID), /schema_version/);
  assert.throws(() => pdfSource(doc, "src:absent"), /has no source "src:absent"/);
  assert.throws(() => pdfSource(doc, OTHER_SOURCE_ID), /needs a pdf source/);
  assert.throws(() => pdfSource("not a document", SOURCE_ID), /must be a JSON object/);
});

test("document: a pdf source without sha256 or without a page count is refused", () => {
  const doc = makeDocument(PDF_SHA256);
  const without = (drop) => ({
    ...doc,
    sources: doc.sources.map((source) => {
      if (source.id !== SOURCE_ID) {
        return source;
      }
      const copy = { ...source };
      delete copy[drop];
      return copy;
    }),
  });
  assert.throws(() => pdfSource(without("sha256"), SOURCE_ID), /carries no sha256/);
  assert.throws(() => pdfSource(without("physical_page_count"), SOURCE_ID),
                /carries no physical_page_count/);
  const short = { ...doc, sources: [{ ...doc.sources[0], sha256: "abc" }, doc.sources[1]] };
  assert.throws(() => pdfSource(short, SOURCE_ID), /carries no sha256/);
});

// --- geometry ---------------------------------------------------------------

test("geometry: a normalized rect with the origin at the top left keeps its coordinates", () => {
  const { doc, index } = fixture();
  const source = doc.sources[0];
  const selector = doc.locations[0].target.selector[0];
  assert.deepEqual(regionQuads(selector, source), [
    [[0.1, 0.1], [0.8, 0.1], [0.8, 0.2], [0.1, 0.2]],
  ]);
  const region = index.pages.get(2).find((entry) => entry.locationId === "loc:0001");
  assert.deepEqual(region.quads, [[[0.1, 0.1], [0.8, 0.1], [0.8, 0.2], [0.1, 0.2]]]);
});

test("geometry: quads with the origin at the bottom left are flipped onto the page", () => {
  const { doc } = fixture();
  const source = doc.sources[0];
  const selector = doc.locations[1].target.selector[0];
  const [quad] = regionQuads(selector, source);
  assert.deepEqual(quad.map(([, y]) => Number(y.toFixed(10))), [0.2, 0.2, 0.3, 0.3]);
  assert.deepEqual(quad.map(([x]) => x), [0.1, 0.9, 0.9, 0.1]);
});

test("geometry: pt coordinates divide by the page's own size, not by page_size", () => {
  const { doc } = fixture();
  const source = doc.sources[0];
  // Page 5 is the 300x400 fold-out of page_sizes; the rest of the run is 600x800.
  assert.deepEqual(pageSizeFor(source, 5), { first: 5, height_pt: 400, last: 5, width_pt: 300 });
  assert.deepEqual(pageSizeFor(source, 4), { height_pt: 800, width_pt: 600 });
  const selector = doc.locations[2].target.selector[0].refinedBy;
  assert.deepEqual(regionQuads(selector, source), [
    [[0.1, 0.1], [0.5, 0.1], [0.5, 0.2], [0.1, 0.2]],
  ]);
});

test("geometry: a pt region with no declared page size is refused, never guessed", () => {
  const { doc } = fixture();
  const sized = doc.sources[0];
  const bare = { ...sized };
  delete bare.page_size;
  delete bare.page_sizes;
  const selector = doc.locations[2].target.selector[0].refinedBy;
  assert.throws(() => regionQuads(selector, bare), /gives no page size/);
});

test("geometry: a malformed, empty or off-page region is refused", () => {
  const { doc } = fixture();
  const source = doc.sources[0];
  const space = {
    origin: "top_left",
    page_box: "CropBox",
    units: "normalized",
    x_axis: "right",
    y_axis: "down",
  };
  const make = (extra) => ({ coordinate_space: space, page: 2, type: "PdfRegionSelector", ...extra });
  assert.throws(() => regionQuads(make({}), source), /neither a rect nor quads/);
  assert.throws(() => regionQuads(make({ rect: { x0: 0.1, x1: 0.1, y0: 0.1, y1: 0.2 } }), source),
                /has no area/);
  assert.throws(() => regionQuads(make({ rect: { x0: 0.1, x1: 1.4, y0: 0.1, y1: 0.2 } }), source),
                /leaves the page/);
  assert.throws(() => regionQuads(make({ rect: { x0: 0.1, x1: "x", y0: 0.1, y1: 0.2 } }), source),
                /non-finite/);
  assert.throws(
    () => regionQuads({ ...make({ rect: { x0: 0, x1: 1, y0: 0, y1: 1 } }), coordinate_space: { ...space, units: "em" } }, source),
    /unknown coordinate units/,
  );
  assert.throws(
    () => regionQuads({ ...make({ rect: { x0: 0, x1: 1, y0: 0, y1: 1 } }), coordinate_space: { ...space, origin: "centre" } }, source),
    /unknown coordinate origin/,
  );
  assert.throws(() => regionQuads({ type: "TextQuoteSelector" }, source), /is a PdfRegionSelector/);
  assert.throws(
    () => regionQuads(make({ quads: [{ x0: 0.1, x1: 0.1, x2: 0.1, x3: 0.1, y0: 0.1, y1: 0.1, y2: 0.1, y3: 0.1 }] }), source),
    /three distinct corners/,
  );
});

test("geometry: a region counts whether it is an alternative, a refinement or a range end", () => {
  const region = { page: 3, type: "PdfRegionSelector" };
  assert.deepEqual(regionSelectors(region), [region]);
  assert.deepEqual(regionSelectors({ refinedBy: region, type: "FragmentSelector", value: "page=3" }),
                   [region]);
  assert.deepEqual(
    regionSelectors({ endSelector: region, startSelector: { type: "TextQuoteSelector" }, type: "RangeSelector" }),
    [region],
  );
  assert.deepEqual(regionSelectors({ type: "TextQuoteSelector" }), []);
  assert.deepEqual(regionSelectors(null), []);
});

test("geometry: a page fraction lands in the PDF.js viewport through the page view box", () => {
  const viewport = { convertToViewportPoint: (x, y) => [x, y] };
  assert.deepEqual(normalizedPointToViewport([0.5, 0.25], [0, 0, 600, 800], viewport), [300, 600]);
  assert.deepEqual(
    quadToViewport([[0, 0], [1, 0], [1, 1], [0, 1]], [0, 0, 600, 800], viewport),
    [[0, 800], [600, 800], [600, 0], [0, 0]],
  );
  assert.throws(() => normalizedPointToViewport([1.5, 0], [0, 0, 600, 800], viewport), /\[0, 1\]/);
  assert.throws(() => normalizedPointToViewport([0.5, 0.5], [0, 0, 600], viewport), /four coordinates/);
  assert.throws(() => normalizedPointToViewport([0.5, 0.5], [0, 0, 0, 800], viewport), /view box is invalid/);
  assert.throws(() => normalizedPointToViewport([0.5, 0.5], [0, 0, 600, 800], {}), /viewport is required/);
  assert.throws(() => quadToViewport([[0, 0]], [0, 0, 600, 800], viewport), /four points/);
});

// --- the index --------------------------------------------------------------

test("index: pages carry the regions of this source only, and name their items", () => {
  const { index } = fixture();
  assert.deepEqual([...index.pages.keys()].sort((a, b) => a - b), [2, 3, 5]);
  assert.equal(index.regions.length, 4);
  assert.equal(index.pageCount, 12);
  assert.equal(index.sha256, PDF_SHA256);
  assert.equal(index.sourceTitle, "Elementary Sets: Lecture Notes (synthetic)");
  assert.deepEqual(index.warnings, []);
  // The site source's own location is not a region of this overlay.
  assert.equal(index.regions.some((region) => region.locationId === "loc:0005"), false);
  assert.equal(index.items.has("exercise-3.1"), false);
  assert.deepEqual([...index.items.keys()].sort(),
                   ["item:definition-2.1", "item:exercise-3.1", "item:remark-4.1", "item:theorem-1.1"]);

  const shared = index.pages.get(2).find((region) => region.locationId === "loc:0004");
  assert.deepEqual(shared.itemIds, ["item:remark-4.1", "item:theorem-1.1"]);
  assert.equal(shared.label, "A remark on unions + Theorem 1.1");
  assert.equal(index.items.get("item:definition-2.1").locator, "Section 2 · pp. 3-4 (physical)");
});

test("index: a region that cannot be placed is dropped with a warning, not guessed", () => {
  const doc = makeDocument(PDF_SHA256);
  const bare = { ...doc.sources[0] };
  delete bare.page_size;
  delete bare.page_sizes;
  const config = readConfig(makeConfig());
  const index = buildIndex({ ...doc, sources: [bare, doc.sources[1]] }, config);
  assert.equal(index.pages.has(5), false);
  assert.equal(index.warnings.length, 1);
  assert.match(index.warnings[0], /^loc:0003: /);
  assert.match(index.warnings[0], /gives no page size/);

  const offPage = {
    ...doc,
    sources: [{ ...doc.sources[0], physical_page_count: 2 }, doc.sources[1]],
  };
  const clipped = buildIndex(offPage, config);
  assert.equal(clipped.warnings.length, 2);
  assert.ok(clipped.warnings.some((line) => /past the source's 2 physical pages/.test(line)));
});

test("index: links are ordered key, attributed, support, example, helper", () => {
  const { index } = fixture();
  const cards = cardsOf(index, "item:theorem-1.1");
  assert.deepEqual(cards.map((card) => card.role),
                   ["key", "attributed", "support", "example", "helper"]);
  assert.deepEqual(cards.map((card) => card.role), LINK_ROLES);
  assert.deepEqual(cards.map((card) => card.declarationId), [
    "Notes.Box.volume_nonneg",
    "Notes.Box.volume_mono",
    "Notes.Set.inter_sub",
    "example:0123456789ab",
    "Notes.Set.union_comm",
  ]);
  // display.strip_prefix hides the shared namespace on the card, never in the title.
  assert.equal(cards[0].name, "Box.volume_nonneg");
  assert.equal(cards[0].fullName, "Notes.Box.volume_nonneg");
});

test("index: an orphaned link is never shown", () => {
  const { index } = fixture();
  const cards = cardsOf(index, "item:definition-2.1");
  assert.deepEqual(cards.map((card) => card.declarationId), ["Notes.Set.inter_sub"]);
  assert.equal(cards.some((card) => card.declarationId === "Notes.Box.volume_retired"), false);
});

test("index: a card carries its snippet, destination, badges and declaration verdict", () => {
  const { index } = fixture();
  const [key, attributed, support, example, helper] = cardsOf(index, "item:theorem-1.1");

  assert.equal(key.href, "../../Notes/Box/index.html#Notes___Box___volume_nonneg");
  assert.equal(key.resolution, "exact");
  assert.equal(key.snippet.text, "theorem volume_nonneg (b : Box) : 0 <= b.volume");
  assert.equal(key.snippet.line, 12);
  assert.deepEqual(key.badges, []);
  assert.deepEqual(key.verdict, {
    declaration: "Notes.Box.volume_nonneg",
    note: "repaired in place",
    status: "fixed",
    verdict: "faithful",
  });

  assert.equal(attributed.href, "../../Notes/Box/index.html");
  assert.equal(attributed.fallbackLabel, "module page (fallback)");
  assert.deepEqual(attributed.badges, ["fallback"]);
  assert.equal(attributed.verdict.verdict, "deviation");

  assert.equal(support.href, "https://notes.example.invalid/api/Notes/Set.html#Notes___Set___inter_sub");
  assert.equal(support.snippet, null);
  assert.equal(support.verdict, null, "a verdict belongs to one item, not to a declaration");

  assert.deepEqual(example.badges, ["anonymous", "unlinked"]);
  assert.equal(example.name, "example:0123456789ab");
  assert.deepEqual(helper.badges, ["sorry: exercise", "unlinked"]);
});

test("index: a link may name a cluster instead of a declaration", () => {
  const { index } = fixture();
  const [key, toCluster] = cardsOf(index, "item:remark-4.1");
  assert.deepEqual(key.badges, ["private", "unlinked"]);
  assert.equal(key.declarationId, "private:Notes.Internal:Notes.Internal.aux");
  assert.deepEqual(toCluster.badges, ["cluster"]);
  assert.equal(toCluster.role, "support");
  assert.equal(toCluster.declarationId, "");
  assert.equal(toCluster.name, "Elementary sets");
  assert.equal(toCluster.cluster.id, "cluster:sets");
  assert.equal(toCluster.href, null);
  assert.equal(toCluster.fallback, false, "a cluster link is a link, not the cluster fallback");
});

test("index: coverage and its note ride on the item", () => {
  const { index } = fixture();
  const item = index.items.get("item:definition-2.1");
  assert.equal(item.coverage, "support_only");
  assert.equal(item.coverageNote,
               "The companion states the neighbouring box lemma, not this definition.");
  assert.equal(index.items.get("item:theorem-1.1").coverage, "");
  // The contract spells the note without the format's extension prefix; both read.
  const plain = buildIndex(
    makeDocument(PDF_SHA256, {
      items: makeDocument(PDF_SHA256).items.map((item2) =>
        item2.id === "item:definition-2.1"
          ? { ...item2, coverage_note: "spelled without the x_ prefix", x_coverage_note: undefined }
          : item2),
    }),
    readConfig(makeConfig()),
  );
  assert.equal(plain.items.get("item:definition-2.1").coverageNote,
               "spelled without the x_ prefix");
});

test("index: an item with no links falls back to its clusters' memberships", () => {
  const { index } = fixture();
  const item = index.items.get("item:exercise-3.1");
  assert.equal(item.usesFallback, true);
  assert.deepEqual(item.clusterIds, ["cluster:sets"]);
  assert.deepEqual(item.clusterTitles, ["Elementary sets"]);
  assert.deepEqual(item.cards.map((card) => [card.role, card.declarationId]), [
    ["primary", "Notes.Set.inter_sub"],
    ["consumer", "Notes.Set.union_comm"],
  ]);
  assert.ok(item.cards.every((card) => card.fallback === true));
  assert.equal(item.cards[0].cluster.title, "Elementary sets");
  // An item with links of its own never falls back.
  assert.equal(index.items.get("item:theorem-1.1").usesFallback, false);
});

// --- coverage and the summary line ------------------------------------------

test("coverage: every value has its own one-line summary, and an unknown one has none", () => {
  for (const value of COVERAGE_VALUES) {
    assert.ok(coverageSummary(value), `${value} has no summary`);
    assert.equal(coverageSummary(value), COVERAGE_SUMMARIES[value]);
    assert.match(coverageSummary(value), /\.$/, `${value}'s summary is not a sentence`);
  }
  // The format's six values, and the two further spellings the contract's section 4 names.
  for (const value of ["formalized", "partial", "support_only", "example_only",
                       "not_formalized", "out_of_scope", "partial_only", "private_only"]) {
    assert.ok(COVERAGE_VALUES.includes(value), `${value} is missing`);
  }
  assert.equal(coverageSummary("mostly_formalised"), "");
  assert.equal(coverageSummary(undefined), "");
});

test("coverage: the stylesheet has a colour for every value and every verdict kind", async () => {
  const css = await readFile(path.join(VIEWER_DIR, "viewer.css"), "utf8");
  for (const value of COVERAGE_VALUES) {
    assert.ok(css.includes(`.target-summary.coverage-${value}`),
              `viewer.css has no rule for coverage ${value}`);
  }
  for (const kind of VERDICT_KINDS) {
    assert.ok(VERDICT_LABELS[kind], `${kind} has no label`);
    assert.ok(css.includes(`.verdict-badge.verdict-${kind}`),
              `viewer.css has no rule for verdict ${kind}`);
  }
  // A fixed entry drops its kind's colour for the neutral one.
  assert.ok(css.includes(".verdict-badge.verdict-fixed"));
});

test("summary: the line above the cards states coverage, the fallback, or a count", () => {
  assert.equal(cardSummaryText({ coverage: "not_formalized", shown: 0 }),
               "Not formalised in this companion.");
  assert.equal(cardSummaryText({ clusters: 2, fallback: true, shown: 3 }),
               "No declaration is linked to this item; showing 3 declarations from 2 clusters it "
               + "belongs to.");
  assert.equal(cardSummaryText({ clusters: 1, fallback: true, shown: 1 }),
               "No declaration is linked to this item; showing 1 declaration from 1 cluster it "
               + "belongs to.");
  assert.equal(cardSummaryText({ shown: 5 }), "5 linked declarations.");
  assert.equal(cardSummaryText({ shown: 1 }), "1 linked declaration.");
  // An unknown coverage value is not a summary: the count still speaks.
  assert.equal(cardSummaryText({ coverage: "invented", shown: 2 }), "2 linked declarations.");
});

// --- hrefs and fragments ----------------------------------------------------

test("href: a relative site path is accepted and anything that could escape is refused", () => {
  assert.equal(safeSitePath("Notes/Box/index.html"), "Notes/Box/index.html");
  assert.equal(safeSitePath("index.html"), "index.html");
  assert.equal(safeSitePath("a.b/c-d_e/index.html"), "a.b/c-d_e/index.html");
  for (const bad of [
    "/etc/passwd",
    "//notes.example.invalid/x.html",
    "../../../secrets.html",
    "a/../../b.html",
    "..",
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "data:text/html,x",
    "https://notes.example.invalid/x.html",
    "Notes\\Box\\index.html",
    "page with space.html",
    "page.html#frag",
    "",
    null,
  ]) {
    assert.equal(safeSitePath(bad), null, `${bad} must be refused`);
  }
});

test("href: only an https URL passes as an absolute destination", () => {
  assert.equal(safeHttpsUrl("https://notes.example.invalid/x.html"),
               "https://notes.example.invalid/x.html");
  for (const bad of [
    "http://notes.example.invalid/x.html",
    "HTTPS://notes.example.invalid/x.html",
    "https://notes.example.invalid/x.html#frag",
    "//notes.example.invalid/x.html",
    "javascript:alert(1)",
    "https://",
    42,
  ]) {
    assert.equal(safeHttpsUrl(bad), null, `${bad} must be refused`);
  }
});

test("href: a destination resolves against the back link's own directory", () => {
  assert.equal(siteRootFrom("../../index.html"), "../../");
  assert.equal(siteRootFrom("../index.html"), "../");
  assert.equal(siteRootFrom("index.html"), "");
  assert.equal(siteRootFrom("/site/index.html"), DEFAULT_SITE_ROOT);
  assert.equal(siteRootFrom("https://notes.example.invalid/"), DEFAULT_SITE_ROOT);
  assert.equal(siteRootFrom(""), DEFAULT_SITE_ROOT);

  const exact = { fragment: "Notes___Box___x", href: "Notes/Box/index.html", resolution: "exact" };
  assert.equal(destinationHref(exact, "../../"), "../../Notes/Box/index.html#Notes___Box___x");
  assert.equal(destinationHref(exact, ""), "Notes/Box/index.html#Notes___Box___x");
  assert.equal(
    destinationHref({ fallback_label: "module page", href: "Notes/Box/index.html", resolution: "fallback" }, "../../"),
    "../../Notes/Box/index.html",
    "a fallback destination links the page, never a fragment",
  );
  assert.equal(destinationHref({ href: "javascript:alert(1)", resolution: "exact" }, "../../"), null);
  assert.equal(destinationHref({ href: "../up.html", resolution: "exact" }, "../../"), null);
  assert.equal(destinationHref({ href: "/root.html", resolution: "exact" }, "../../"), null);
  assert.equal(destinationHref({ href: "//host/x.html", resolution: "exact" }, "../../"), null);
  assert.equal(destinationHref(null, "../../"), null);
});

test("href: the back link keeps its .. but never a scheme the config smuggled in", () => {
  assert.equal(safeBackLinkHref("../../index.html"), "../../index.html");
  assert.equal(safeBackLinkHref("index.html"), "index.html");
  assert.equal(safeBackLinkHref("https://notes.example.invalid/"),
               "https://notes.example.invalid/");
  for (const bad of [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "data:text/html,<script>x</script>",
    "http://notes.example.invalid/",
    "//notes.example.invalid/x.html",
    "/site/index.html",
    "..\\..\\index.html",
    "index.html#top",
    "   ",
    "",
    null,
  ]) {
    assert.equal(safeBackLinkHref(bad), null, `${bad} must be refused`);
  }
  // readConfig is where the rule bites: a refused href becomes the default, so
  // the page header can never carry a live javascript: link.
  const evil = readConfig(makeConfig({
    back_link: { href: "javascript:alert(1)", text: "Back" },
  }));
  assert.equal(evil.backLink.href, DEFAULT_BACK_LINK.href);
  assert.equal(evil.backLink.text, "Back");
  assert.equal(evil.siteRoot, DEFAULT_SITE_ROOT);
  const absolute = readConfig(makeConfig({ back_link: { href: "/site/index.html" } }));
  assert.equal(absolute.backLink.href, DEFAULT_BACK_LINK.href);
  // An accepted back link one directory up moves the root destinations resolve
  // against with it.
  const nearer = readConfig(makeConfig({ back_link: { href: "../index.html" } }));
  assert.equal(nearer.backLink.href, "../index.html");
  assert.equal(nearer.siteRoot, "../");
  const remote = readConfig(makeConfig({
    back_link: { href: "https://notes.example.invalid/" },
  }));
  assert.equal(remote.backLink.href, "https://notes.example.invalid/");
  assert.equal(remote.siteRoot, DEFAULT_SITE_ROOT, "an https back link is no site root");
});

test("href: a fragment is percent-encoded before it reaches the URL", () => {
  assert.equal(encodeFragment("Notes___Box___volume_nonneg"), "Notes___Box___volume_nonneg");
  assert.equal(encodeFragment("a b"), "a%20b");
  assert.equal(encodeFragment("a#b"), "a%23b");
  assert.equal(encodeFragment('x"onload="y'), "x%22onload%3D%22y");
  assert.equal(encodeFragment("α"), "%CE%B1");
  assert.equal(encodeFragment(""), "");
  assert.equal(encodeFragment(undefined), "");
  assert.equal(
    destinationHref({ fragment: 'a"b', href: "x.html", resolution: "exact" }, ""),
    "x.html#a%22b",
  );
});

// --- verdicts ---------------------------------------------------------------

test("verdicts: a fixed entry reads as history, an open one keeps its kind", () => {
  assert.deepEqual(verdictDisplay("deviation", "fixed"),
                   { className: "verdict-fixed", label: "fixed: statement deviation" });
  assert.deepEqual(verdictDisplay("deviation", "unconfirmed"),
                   { className: "verdict-deviation", label: "statement deviation" });
  assert.deepEqual(verdictDisplay("proxy", "human_confirmed"),
                   { className: "verdict-proxy", label: "proxy definition" });
  assert.deepEqual(verdictDisplay("attribution_gap", "contested"),
                   { className: "verdict-attribution_gap", label: "attribution gap" });
  assert.deepEqual(verdictDisplay("convention", "fixed"),
                   { className: "verdict-fixed", label: "fixed: convention delta" });
});

test("verdicts: a repaired declaration reads faithful and an unchanged one superseded", () => {
  assert.deepEqual(declarationVerdictDisplay("faithful", "fixed"),
                   { className: "verdict-faithful", label: "faithful" });
  assert.deepEqual(declarationVerdictDisplay("deviation", "fixed"),
                   { className: "verdict-deviation", label: "statement deviation, superseded" });
  assert.deepEqual(declarationVerdictDisplay("attribution_gap", "fixed"),
                   { className: "verdict-attribution_gap", label: "attribution gap, superseded" });
  assert.deepEqual(declarationVerdictDisplay("deviation", "unconfirmed"),
                   { className: "verdict-deviation", label: "statement deviation" });
  assert.deepEqual(declarationVerdictDisplay("proxy", "human_confirmed"),
                   { className: "verdict-proxy", label: "proxy definition" });
});

test("verdicts: a resolution adds one line, and an open verdict adds none", () => {
  assert.equal(resolutionText({ pr: 7, state: "fixed" }), "fixed, PR #7");
  assert.equal(resolutionText({ branch: "a-branch", state: "in_progress" }),
               "in progress, branch a-branch");
  assert.equal(resolutionText({ issue: 3, note: "by design", state: "argued_correct" }),
               "argued correct, issue #3, by design");
  assert.equal(resolutionText({ state: "open" }), "");
  assert.equal(resolutionText(undefined), "");
});

// --- the PDF's bytes --------------------------------------------------------

test("pdf: the bytes are verified against the source record's sha256", async () => {
  const good = await verifyPdfBytes(PDF_BYTES, PDF_SHA256);
  assert.equal(good.ok, true);
  assert.equal(good.actual, PDF_SHA256);
  assert.equal(good.expected, PDF_SHA256);

  const other = fakeSha256("0");
  const bad = await verifyPdfBytes(PDF_BYTES, other);
  assert.equal(bad.ok, false);
  assert.equal(bad.actual, PDF_SHA256);
  assert.equal(bad.expected, other);

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
  // The module is searched too, so the anonymous example in Notes/Set.lean matches.
  assert.equal(cardsMatching(cards, { query: "Notes.Set" }).length, 3);
  assert.equal(cardsMatching(cards, { query: "nothing here" }).length, 0);
  assert.equal(cardsMatching(cards, {}).length, 5);

  const page = paginateCards(cards, 2, 2);
  assert.equal(page.page, 2);
  assert.equal(page.pageCount, 3);
  assert.equal(page.total, 5);
  assert.deepEqual(page.items.map((card) => card.role), ["support", "example"]);
  assert.equal(paginateCards(cards, 99, 2).page, 3);
  assert.equal(paginateCards([], 1, 2).pageCount, 1);
  assert.throws(() => paginateCards(cards, 1, 0), /positive integer/);

  assert.deepEqual(groupCards(cards).map((group) => group.role), LINK_ROLES);
  assert.deepEqual(groupCards([]), []);
  const doubled = groupCards([cards[0], { ...cards[1], role: "key" }, cards[2]]);
  assert.deepEqual(doubled.map((group) => [group.role, group.cards.length]),
                   [["key", 2], ["support", 1]]);
});
