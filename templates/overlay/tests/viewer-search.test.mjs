import assert from "node:assert/strict";
import test from "node:test";
import { searchablePage, findPageMatches, createPdfTextIndex, searchTextRange } from "../runtime/viewer/viewer-search.js";

test("search finds phrases across items and line endings, preserving raw text offsets", () => {
  const page = searchablePage([{ str: "A path in", hasEOL: true }, { str: "the space." }]);
  assert.deepEqual(findPageMatches(page, "PATH  IN the"), [{ start: 2, end: 12 }]);
  assert.deepEqual(findPageMatches(page, ""), []);
  assert.deepEqual(findPageMatches(page, "."), [{ start: 18, end: 19 }], "queries are literal, not regular expressions");
});

test("search handles ligatures, soft hyphens, supplementary characters and printed line hyphens", () => {
  const page = searchablePage([{ str: "😀 ﬁnite homo-", hasEOL: true }, { str: "topy co\u00adver" }]);
  assert.deepEqual(findPageMatches(page, "FINITE"), [{ start: 3, end: 8 }]);
  assert.deepEqual(findPageMatches(page, "homotopy"), [{ start: 9, end: 18 }]);
  assert.deepEqual(findPageMatches(page, "cover"), [{ start: 19, end: 25 }]);
  assert.deepEqual(findPageMatches(page, "😀"), [{ start: 0, end: 2 }]);
  assert.deepEqual(findPageMatches(searchablePage([{ str: "x-y" }]), "xy"), [], "ordinary hyphens stay significant");
});

test("search scans every page, caches extracted text and reports unreadable pages", async () => {
  const reads = [];
  const pdf = { numPages: 3, async getPage(page) {
    return { async getTextContent(options) {
      reads.push([page, options]);
      if (page === 2) throw new Error("unreadable");
      return { items: [{ str: page === 1 ? "path path" : "Path" }] };
    } };
  } };
  const index = createPdfTextIndex(pdf);
  const progress = [];
  assert.deepEqual(await index.search("path", { onProgress: (n) => progress.push(n) }), {
    matches: [{ page: 1, start: 0, end: 4 }, { page: 1, start: 5, end: 9 }, { page: 3, start: 0, end: 4 }],
    failedPages: [2], textPages: 2,
  });
  assert.deepEqual(progress, [1, 2, 3]);
  await index.search("other");
  assert.deepEqual(reads.map(([page]) => page), [1, 2, 3, 2], "successful text is cached; failures can retry");
  assert.ok(reads.every(([, options]) => options.disableNormalization === true));
});

test("a cancelled search stops before requesting more pages or publishing progress", async () => {
  let cancelled = false;
  const progress = [];
  const index = createPdfTextIndex({ numPages: 3, async getPage(page) {
    assert.equal(page, 1);
    return { async getTextContent() { cancelled = true; return { items: [{ str: "path" }] }; } };
  } });
  assert.equal(await index.search("path", { cancelled: () => cancelled, onProgress: (n) => progress.push(n) }), null);
  assert.deepEqual(progress, []);
});

test("a match spanning PDF text items maps to one DOM range without changing selectable text", () => {
  const a = { firstChild: { textContent: "path " } };
  const b = { firstChild: { textContent: "space" } };
  const points = [];
  const doc = { createRange: () => ({ setStart: (...p) => points.push(p), setEnd: (...p) => points.push(p) }) };
  searchTextRange(doc, { textDivs: [a, b], textContentItemsStr: ["path ", "space"] }, { start: 2, end: 8 });
  assert.deepEqual(points, [[a.firstChild, 2], [b.firstChild, 3]]);
  assert.equal(a.firstChild.textContent, "path ");
});
