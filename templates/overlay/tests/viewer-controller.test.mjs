/**
 * The overlay viewer's DOM controller, through its injectable seams.
 *
 * Every run here drives `bootstrapViewer` with a fake document built from the
 * real `runtime/viewer/index.html`, a fetch that serves a synthetic
 * `overlay.config.json`, a synthetic `GENERATED.json`, a synthetic bundle of
 * Literature Links annotations and synthetic PDF bytes, and a fake PDF.js. The pure half is
 * covered in viewer-core.test.mjs.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { NAMESPACE, sha256Hex } from "../runtime/viewer/viewer-core.js";
import { bootstrapViewer, isViewerShortcut, rememberedPage } from "../runtime/viewer/viewer.js";
import {
  BUNDLE_URL,
  ORIGIN,
  PAGE_URL,
  PDF_BYTES,
  PDF_ROUTE,
  fakeDocument,
  fakePdfjs,
  fakeSha256,
  fire,
  jsonResponse,
  makeConfig,
  bundleText,
  makeGenerated,
  linkRecord,
  makeSections,
  memoryStorage,
  relinked,
  notFound,
  shellHtml,
  throwingStorage,
} from "./fixtures.mjs";

const PDF_SHA256 = await sha256Hex(PDF_BYTES);
const SHELL = await shellHtml();

function pdfResponse(bytes = PDF_BYTES, { contentType = "application/pdf", ok = true, status = 200 } = {}) {
  return jsonResponse(bytes, { contentType, ok, status });
}

async function start({
  config = makeConfig(),
  sections = makeSections(PDF_SHA256),
  generated = undefined,
  documentRef = fakeDocument(SHELL),
  locationRef = { hash: "" },
  pdf = () => pdfResponse(),
  pdfjs = fakePdfjs(),
  served = undefined,
  storage = memoryStorage(),
  windowRef = undefined,
} = {}) {
  const record = generated === undefined ? await makeGenerated(sections) : generated;
  const urls = [];
  const historyRef = { replaceState: (_state, _title, url) => urls.push(url) };
  const fetched = [];
  const fetchImpl = async (url, options) => {
    fetched.push({ options, url });
    if (url === "../overlay.config.json") {
      return config === null ? notFound() : jsonResponse(config);
    }
    if (url === "../GENERATED.json") {
      return record === null ? notFound() : jsonResponse(record);
    }
    if (url === PDF_ROUTE) {
      return pdf();
    }
    if (url === BUNDLE_URL) {
      return jsonResponse(served ?? bundleText(sections));
    }
    throw new Error(`the viewer asked for ${url}`);
  };
  const result = await bootstrapViewer({
    cryptoProvider: globalThis.crypto,
    documentRef,
    fetchImpl,
    historyRef,
    locationRef,
    pageUrl: PAGE_URL,
    pdfjsLoader: async () => pdfjs,
    storage,
    windowRef,
  });
  return { documentRef, fetched, hashes: urls, pdfjs, result, storage };
}

function el(documentRef, id) {
  return documentRef.elements.get(id);
}

/** The text a node shows: its own, or its children's joined (strings count as text). */
function textOf(node) {
  if (typeof node === "string") {
    return node;
  }
  return (node?.textContent ?? "") + (node?.children ?? []).map(textOf).join("");
}

function polygonOf(documentRef, regionId) {
  return el(documentRef, "overlay").children.find((child) => child.dataset.regionId === regionId);
}

function indexRows(documentRef) {
  return el(documentRef, "page-index").children.map((li) => li.children[0]);
}

function itemTitle(documentRef) {
  return el(documentRef, "item-label").children[0]?.textContent;
}

function rows(documentRef, id) {
  return el(documentRef, id).children;
}

function rowName(row) {
  return textOf(row.children[0].children[0]);
}

function rowKind(row) {
  return textOf(row.children[0].children[1]);
}

/** The key cards, as their `[eyebrow, name, statement, ...]` children. */
function keyCards(documentRef) {
  return el(documentRef, "key-list").children.map((card) => card.children);
}

/** Every card verdict shown in the item view, as `text|class`. */
function verdictMarks(documentRef) {
  const found = [];
  const walk = (node) => {
    if (typeof node !== "object" || !node) {
      return;
    }
    if (String(node.className).includes("card-verdict")) {
      found.push(`${node.textContent}|${node.className}`);
    }
    (node.children ?? []).forEach(walk);
  };
  ["key-list", "related-list"].forEach((id) => walk(el(documentRef, id)));
  return found;
}

async function turnTo(documentRef, page) {
  const input = el(documentRef, "page-number");
  input.value = String(page);
  await fire(input, "change");
}

async function selectPageRegion(documentRef, state, regionId) {
  const region = state.index.regions.find((entry) => entry.id === regionId);
  assert.ok(region, "no such region");
  const input = el(documentRef, "page-number");
  input.value = String(region.page);
  await fire(input, "change");
  const polygon = el(documentRef, "overlay").children.find((child) => child.dataset.regionId === region.id);
  assert.ok(polygon, `${region.id} has no polygon on page ${region.page}`);
  await fire(polygon, "click");
  return region;
}

// --- start-up ---------------------------------------------------------------

test("start-up: the config, GENERATED.json, the one bundle and the route are fetched", async () => {
  const { fetched, pdfjs } = await start();
  assert.deepEqual(fetched.map((call) => call.url),
                   ["../overlay.config.json", "../GENERATED.json", BUNDLE_URL, PDF_ROUTE]);
  for (const call of fetched) {
    assert.deepEqual(call.options, { cache: "no-store", credentials: "same-origin" });
  }
  assert.equal(pdfjs.calls.length, 1);
  assert.equal(pdfjs.calls[0].isEvalSupported, false);
  assert.deepEqual(pdfjs.calls[0].data, PDF_BYTES);
});

test("start-up: the shell's placeholders are replaced from the configuration", async () => {
  const { documentRef } = await start();
  assert.equal(documentRef.title, "Elementary sets: source overlay (synthetic)");
  assert.equal(el(documentRef, "page-heading").textContent, "Elementary sets: source overlay (synthetic)");
  assert.equal(el(documentRef, "page-lede").textContent, "Regions on the source, declarations in the companion.");
  assert.equal(el(documentRef, "back-link").textContent, "Back to the notes");
  assert.equal(el(documentRef, "back-link").attributes.href, "../../index.html");
  assert.equal(el(documentRef, "back-link").hidden, false);
  assert.equal(el(documentRef, "workspace").hidden, false);
  assert.equal(el(documentRef, "error").hidden, true);
  assert.equal(el(documentRef, "notice").hidden, true);
  assert.equal(el(documentRef, "status").textContent, "Verified PDF");
  assert.equal(el(documentRef, "status-chip").classList.contains("visually-hidden"), false);
  assert.equal(el(documentRef, "keys").hidden, false);
  assert.deepEqual([el(documentRef, "page-number").value, el(documentRef, "page-count").textContent], ["1", "/ 12"]);
  assert.equal(el(documentRef, "page-stage").hidden, false, "the page shows once it is drawn");
});

test("start-up: refused data leaves an error on the page, not a blank one", async () => {
  const sections = makeSections(PDF_SHA256);
  for (const [options, pattern] of [
    [{ config: makeConfig({ pdf: { route: "/files/notes.pdf" } }) }, /pdf\.route/],
    [{ generated: null }, /Unable to load \.\.\/GENERATED\.json \(404\)/],
    [{ generated: {} }, /no annotations block/],
    [{ config: makeConfig({ source_id: "src:nowhere" }) }, /No annotation is a record of the PDF "src:nowhere"/],
    [{ served: "{}\n" }, /annotations\.bundle\.json has sha256 [a-f\d]{64}; GENERATED\.json records/],
  ]) {
    const documentRef = fakeDocument(SHELL);
    const generated = options.generated === undefined ? await makeGenerated(sections) : options.generated;
    await assert.rejects(() => start({ ...options, documentRef, generated, sections }), pattern);
    assert.equal(el(documentRef, "error").hidden, false);
    assert.match(el(documentRef, "error").textContent, pattern);
    assert.equal(el(documentRef, "workspace").hidden, true);
    assert.equal(el(documentRef, "status").textContent, "Data not loaded");
  }
});

test("start-up: a bundle that matches its sha256 but is not a bundle is refused", async () => {
  const sections = makeSections(PDF_SHA256);
  for (const [text, pattern] of [["{oops\n", /annotations\.bundle\.json is not JSON/],
                                 ["[]\n", /is not a formal-companion\/overlay-annotations bundle/]]) {
    const generated = await makeGenerated(sections, text);
    await assert.rejects(() => start({ generated, sections, served: text }), pattern);
  }
});

test("start-up: a card's page is under the site root, and an end from elsewhere under its own file", async () => {
  const sections = makeSections(PDF_SHA256);
  const record = linkRecord(sections, "item:theorem-1.1", "key", "Notes.Box.volume_nonneg");
  record.object[0].extensions[NAMESPACE].destination.href = "beside.html";
  sections[2].find((entry) => entry.id === "plain-link").object[0].uri = "beside.html";
  const { result } = await start({ sections });
  const [key] = result.state.index.items.get("item:theorem-1.1").cards;
  assert.equal(key.href, `${ORIGIN}/beside.html#Notes___Box___volume_nonneg`);
  assert.equal(result.state.index.items.get("plain-link").cards[0].href, `${ORIGIN}/annotations/2/beside.html`);
});

test("start-up: a record that cannot be placed is reported in the page notice", async () => {
  const sections = makeSections(PDF_SHA256);
  sections[2][0].subject[0].location = { page: 5, rect: [0.9, 0.1, 0.4, 0.1] };
  const { documentRef, result } = await start({ sections });
  assert.equal(el(documentRef, "notice").hidden, false);
  assert.match(el(documentRef, "notice").textContent, /^ⓘ 1 region\(s\) could not be placed/);
  assert.equal(result.state.index.pages.has(5), false);
  assert.equal(el(documentRef, "workspace").hidden, false, "the rest of the overlay still opens");
});

// --- the PDF ----------------------------------------------------------------

test("pdf: a hash mismatch stops before PDF.js and says so on the page", async () => {
  const pdfjs = fakePdfjs();
  const { documentRef, result } = await start({ pdfjs, sections: makeSections(fakeSha256("9")) });
  assert.equal(pdfjs.calls.length, 0, "PDF.js must not see unverified bytes");
  assert.equal(result.state.pdfDocument, null);
  assert.equal(el(documentRef, "workspace").hidden, true);
  assert.match(el(documentRef, "error").textContent, /Loopback PDF SHA-256 is/);
  assert.equal(el(documentRef, "status").textContent, "PDF not verified");
  assert.equal(el(documentRef, "status-glyph").textContent, "!");
});

test("pdf: a 404, a wrong media type and a wrong byte count are all refused", async () => {
  for (const [pdf, pattern] of [
    [() => pdfResponse(PDF_BYTES, { ok: false, status: 404 }), /loopback PDF \(404\)/],
    [() => pdfResponse(PDF_BYTES, { contentType: "text/html" }), /unexpected media type text\/html/],
    [() => pdfResponse(new TextEncoder().encode("short")), /the source record says \d+/],
  ]) {
    const { documentRef } = await start({ pdf, pdfjs: fakePdfjs() });
    assert.match(el(documentRef, "error").textContent, pattern);
    assert.equal(el(documentRef, "workspace").hidden, true);
  }
});

// --- the page index ---------------------------------------------------------

test("index: with nothing selected the panel lists the page's passages", async () => {
  const { documentRef } = await start();
  assert.equal(el(documentRef, "selection-empty").hidden, false);
  assert.equal(el(documentRef, "index-heading").textContent, "Page 1");
  assert.equal(el(documentRef, "index-meta").textContent, "No highlighted passages on this page.");
  assert.equal(el(documentRef, "page-index").hidden, true);
  await turnTo(documentRef, 2);
  assert.equal(el(documentRef, "index-heading").textContent, "Page 2");
  assert.equal(el(documentRef, "index-meta").textContent, "2 passages · 2 with a key declaration");
  assert.equal(el(documentRef, "page-index").attributes["aria-label"], "Passages on page 2");
  assert.deepEqual(indexRows(documentRef).map((row) => [textOf(row.children[1]), textOf(row.children[2])]), [
    ["Theorem 1.1", "Box.volume_nonneg +4"],
    ["A remark on unions", "Internal.aux"],
  ]);
  await turnTo(documentRef, 5);
  assert.deepEqual(indexRows(documentRef).map((row) => textOf(row.children[2])), ["No declaration linked"]);
  await turnTo(documentRef, 3);
  assert.deepEqual(indexRows(documentRef).map((row) => textOf(row.children[2])), ["Support only"]);
});

test("index: a row lights its region, a region lights its row and shows a peek, and a click selects", async () => {
  const { documentRef, result } = await start();
  await turnTo(documentRef, 2);
  const [row] = indexRows(documentRef);
  const polygon = polygonOf(documentRef, "loc:0001");
  await fire(row, "mouseenter");
  assert.match(polygon.className, /\bis-hot\b/);
  await fire(row, "mouseleave");
  assert.doesNotMatch(polygon.className, /\bis-hot\b/);
  await fire(polygon, "pointerenter", { pointerType: "mouse" });
  assert.match(row.className, /\bis-hot\b/);
  const peek = el(documentRef, "region-peek");
  assert.equal(peek.hidden, false);
  assert.deepEqual(peek.children.map(textOf), ["Theorem 1.1", "Box.volume_nonneg", "+4"]);
  assert.deepEqual([peek.style.left, peek.style.top], ["150px", "94px"]);
  await fire(polygon, "pointerleave");
  assert.equal(peek.hidden, true);
  await fire(polygon, "pointerenter", { pointerType: "touch" });
  assert.equal(peek.hidden, true, "touch has no hover");
  await fire(row, "click");
  assert.equal(result.state.region.id, "loc:0001");
  assert.equal(itemTitle(documentRef), "Theorem 1.1");
  await fire(polygon, "pointerenter", { pointerType: "mouse" });
  assert.equal(peek.hidden, true, "the selected passage gets no peek");
  assert.doesNotMatch(polygon.className, /\bis-hot\b/, "and is not lit by its own hover");
});

test("index: a row's mark is the region's swatch, and its name stays on one line", async () => {
  const { documentRef } = await start();
  await turnTo(documentRef, 2);
  const [row] = indexRows(documentRef);
  assert.deepEqual([row.children[0].className, row.children[0].title], ["swatch mini formalized", "Formalized"]);
  const walk = (node) => (typeof node === "object" ? [node.tagName, ...(node.children ?? []).flatMap(walk)] : []);
  assert.ok(!walk(row.children[2]).includes("WBR"), "no break points in an index row's name");
  await turnTo(documentRef, 3);
  assert.equal(indexRows(documentRef)[0].children[0].className, "swatch mini partial");
});

test("index: a page with no passages offers the nearest pages that have some", async () => {
  const { documentRef, result } = await start();
  const pages = [...result.state.index.pages.keys()].sort((a, b) => a - b);
  const jump = el(documentRef, "page-jump");
  assert.equal(jump.hidden, false);
  assert.deepEqual(jump.children.map((button) => button.textContent), [`Next passage: p. ${pages[0]} ›`]);
  await fire(jump.children[0], "click");
  assert.equal(result.state.currentPage, pages[0]);
  assert.equal(jump.hidden, true, "a page with passages needs no jump");
  const after = pages.at(-1) + 1;
  await turnTo(documentRef, after);
  assert.equal(result.state.currentPage, after);
  assert.deepEqual(jump.children.map((button) => button.textContent), [`‹ Previous passage: p. ${pages.at(-1)}`]);
});

// --- the item view ----------------------------------------------------------

test("item: the head, then each key card with its statement, then the related rows", async () => {
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0001");
  assert.equal(el(documentRef, "selection").hidden, false);
  assert.equal(el(documentRef, "selection-empty").hidden, true);
  assert.equal(itemTitle(documentRef), "Theorem 1.1");
  assert.equal(el(documentRef, "item-label").children.length, 1, "no subtitle");
  assert.equal(el(documentRef, "item-locator").textContent, "Chapter 1", "the label is not repeated");
  assert.equal(el(documentRef, "item-locator").title, "Chapter 1, Theorem 1.1");
  const pill = el(documentRef, "target-summary");
  assert.deepEqual([pill.textContent, pill.title, pill.className, pill.hidden],
                   ["Formalized", "Formalized: a key declaration states it.", "target-summary coverage-formalized", false]);
  assert.equal(keyCards(documentRef).length, 1);
  const [[eyebrow, name, pre]] = keyCards(documentRef);
  assert.equal(textOf(eyebrow.children[0]), "theorem");
  const link = eyebrow.children[1];
  assert.deepEqual([link.tagName, link.textContent, link.attributes.href, link.title],
                   ["A", "View in source ↗", `${ORIGIN}/Notes/Box/index.html#Notes___Box___volume_nonneg`,
                    "Notes.Box.volume_nonneg"]);
  assert.equal(textOf(name), "Box.volume_nonneg");
  assert.equal(pre.tagName, "PRE");
  assert.equal(textOf(pre), "theorem volume_nonneg (b : Box) : 0 <= b.volume");
  const [line] = pre.children;
  assert.equal(line.className, "ln", "each source line is a block");
  assert.deepEqual([line.children[0].className, line.children[0].textContent], ["tk-kw", "theorem"]);
  assert.equal(el(documentRef, "key-count").hidden, true, "one key card needs no count");
  assert.equal(el(documentRef, "key-head").hidden, true, "and with no filter either, no line for them");
  assert.equal(el(documentRef, "key-more").hidden, true);
  assert.equal(el(documentRef, "key-note").hidden, true);
  assert.equal(el(documentRef, "related-section").hidden, false);
  assert.equal(el(documentRef, "related-count").textContent, "4");
  assert.deepEqual(rows(documentRef, "related-list").map((row) => [rowName(row), rowKind(row)]), [
    ["Box.volume_mono", "attributed · theorem · module page"],
    ["Set.inter_sub", "support · theorem"],
    ["example:0123456789ab", "example · no page"],
    ["Set.union_comm", "helper · theoremsorry"],
  ]);
  assert.equal(el(documentRef, "related-pager").hidden, true, "one page of rows has no pager");
  assert.equal(el(documentRef, "target-filter").hidden, true, "five cards need no filter");
  assert.equal(el(documentRef, "provenance-note").textContent, "Curation and pipeline links, partly reviewed.");
  assert.equal(el(documentRef, "region-bar").hidden, false);
  assert.deepEqual([el(documentRef, "region-bar").style.top, el(documentRef, "region-bar").style.height],
                   ["100px", "100px"]);
  // The region is drawn through the displayed page: a quarter of 600 pt across, an eighth of 800 down.
  const polygon = polygonOf(documentRef, "loc:0001");
  assert.equal(polygon.attributes.points, "150,100 450,100 450,200 150,200");
  assert.equal(polygon.dataset.coverage, "formalized");
  assert.match(el(documentRef, "overlay").className, /\bhas-selection\b/);
});

test("item: a related row is one plain link to its source, with no statement", async () => {
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0001");
  const [mono, , example, helper] = rows(documentRef, "related-list");
  const link = mono.children[0];
  assert.deepEqual([mono.children.length, link.tagName, link.attributes.href, link.attributes["aria-expanded"]],
                   [1, "A", `${ORIGIN}/Notes/Box/index.html`, undefined]);
  assert.equal(link.title, "Notes.Box.volume_mono\nmodule page (fallback)");
  assert.equal(rowKind(mono), "attributed · theorem · module page");
  assert.ok(!JSON.stringify(rows(documentRef, "related-list").map(textOf)).includes("theorem volume"),
            "no statement in a related row");
  const unlinked = example.children[0];
  assert.deepEqual([unlinked.tagName, unlinked.className, unlinked.attributes.href],
                   ["SPAN", "related-link is-unlinked", undefined]);
  assert.equal(unlinked.title, "example:0123456789ab\nanonymous\nNo rendered page");
  assert.equal(rowKind(example), "example · no page", "a row with nowhere to go says so, and its kind once");
  assert.equal(rowKind(helper), "helper · theoremsorry", "a sorry is tagged on the row");
  assert.equal(helper.children[0].title, "Notes.Set.union_comm\nsorry: exercise");
});

test("item: past eight key cards the rest wait behind a button that hands focus on", async () => {
  const sections = makeSections(PDF_SHA256);
  const key = linkRecord(sections, "item:theorem-1.1", "key", "Notes.Box.volume_nonneg");
  for (let n = 0; n < 11; n += 1) {
    const name = `Notes.Part.p${String(n).padStart(2, "0")}`;
    sections[1].push(relinked(key, name, { kind: "theorem" }));
  }
  const documentRef = fakeDocument(SHELL);
  const focused = [];
  const create = documentRef.createElement;
  documentRef.createElement = (tag) => Object.assign(create(tag), { focus() { focused.push(this); } });
  const { result } = await start({ documentRef, sections });
  await selectPageRegion(documentRef, result.state, "loc:0001");
  assert.deepEqual([el(documentRef, "key-count").hidden, el(documentRef, "key-count").textContent],
                   [false, "12 key declarations"]);
  assert.equal(el(documentRef, "key-head").hidden, false, "the count's line shows");
  assert.equal(keyCards(documentRef).length, 8);
  assert.ok(keyCards(documentRef).every((card) => card[2]?.tagName === "PRE"), "every key card has its statement");
  const more = el(documentRef, "key-more");
  assert.deepEqual([more.hidden, more.textContent], [false, "Show 4 more"]);
  await fire(more, "click");
  assert.equal(keyCards(documentRef).length, 12);
  assert.equal(more.hidden, true);
  assert.deepEqual(focused.map((node) => textOf(node.children[1])), ["Part.p07"],
                   "focus moves to the first card the button showed");
});

test("item: a statement is clamped by the lines it wraps to, not only by its source lines", async () => {
  const statement = async (text) => {
    const sections = makeSections(PDF_SHA256);
    const key = linkRecord(sections, "item:theorem-1.1", "key", "Notes.Box.volume_nonneg");
    key.object[0].extensions[NAMESPACE].snippet.text = text;
    const { documentRef, result } = await start({ sections });
    await selectPageRegion(documentRef, result.state, "loc:0001");
    const [[, , pre, button]] = keyCards(documentRef);
    return [pre.className, button?.textContent];
  };
  assert.deepEqual(await statement(`theorem long : ${"a + ".repeat(250)}a = a`), ["lean is-clamped", "Show all"],
                   "one 1000-character line wraps far past fourteen lines");
  assert.deepEqual(await statement(Array.from({ length: 14 }, (_, n) => `  h${n} : a = a`).join("\n")),
                   ["lean", undefined], "fourteen short lines fit");
  assert.deepEqual(await statement(Array.from({ length: 15 }, (_, n) => `  h${n} : a = a`).join("\n")),
                   ["lean is-clamped", "Show all"]);
});

test("item: a line's closing colon is kept with the word before it", async () => {
  const text = "theorem t (x : ℝ) (hx : 0 < x) :\n    (h : Prop) :\n    a_name_longer_than_sixteen :\n    x = x :=\n  rfl :";
  const sections = makeSections(PDF_SHA256);
  const key = linkRecord(sections, "item:theorem-1.1", "key", "Notes.Box.volume_nonneg");
  key.object[0].extensions[NAMESPACE].snippet.text = text;
  const { documentRef, result } = await start({ sections });
  await selectPageRegion(documentRef, result.state, "loc:0001");
  const [[, , pre]] = keyCards(documentRef);
  assert.equal(textOf(pre), text, "the statement's text is the snippet's, exactly");
  const kept = pre.children.map((line) => line.children.filter((child) => child.className === "nb"));
  assert.deepEqual(kept.map((spans) => spans.map(textOf)), [["x) :"], ["Prop) :"], [], [], ["rfl :"]]);
  assert.deepEqual(kept[1][0].children.map((child) => child.className ?? child), ["tk-kw", ") :"]);
});

test("item: verdicts stay hidden until the faithfulness switch is pressed, and the choice is remembered", async () => {
  const storage = memoryStorage();
  const { documentRef, result } = await start({ storage });
  const button = el(documentRef, "toggle-faithfulness");
  await selectPageRegion(documentRef, result.state, "loc:0001");
  const label = button.textContent;
  assert.deepEqual([button.hidden, button.attributes["aria-pressed"]], [false, "false"]);
  assert.deepEqual([el(documentRef, "verdict-notes").children.length, verdictMarks(documentRef).length], [0, 0]);
  assert.equal(polygonOf(documentRef, "loc:0001").dataset.verdict, undefined);
  await fire(button, "click");
  assert.deepEqual([button.attributes["aria-pressed"], button.textContent], ["true", label], "the label never changes");
  assert.equal(el(documentRef, "verdict-notes").children.length, 1);
  assert.deepEqual(verdictMarks(documentRef), ["faithful|card-verdict verdict-badge verdict-faithful"]);
  assert.equal(polygonOf(documentRef, "loc:0001").dataset.verdict, "fixed");
  assert.equal(storage.getItem("notes-overlay-page-faithfulness"), "1");
  const again = await start({ storage });
  assert.equal(again.result.state.faithfulness, true);
  await fire(el(again.documentRef, "toggle-faithfulness"), "click");
  assert.deepEqual([again.result.state.faithfulness, storage.getItem("notes-overlay-page-faithfulness")], [false, "0"]);
});

test("item: a verdict is one line of kind, summary and resolution, and the footer says it is unreviewed", async () => {
  const { documentRef, result } = await start();
  await fire(el(documentRef, "toggle-faithfulness"), "click");
  await selectPageRegion(documentRef, result.state, "loc:0001");
  const [line] = el(documentRef, "verdict-notes").children;
  assert.deepEqual(line.children.map((child) => `${child.textContent}|${child.className}`), [
    "fixed: statement deviation (medium)|verdict-badge verdict-fixed",
    "The box bound was stated for closed boxes only.|verdict-summary",
    "PR #7|faint",
  ]);
  assert.equal(line.title, "Notes/Box.lean:12\nsource page 2\ntwo independent readings and a reconciler\nadjudication");
  assert.doesNotMatch(textOf(line), /Faithfulness:|unconfirmed|\[/);
  assert.deepEqual(verdictMarks(documentRef), ["faithful|card-verdict verdict-badge verdict-faithful"]);
  const [dot] = rows(documentRef, "related-list")[0].children[0].children[1].children;
  assert.equal(dot.className, "dot verdict-dot verdict-deviation", "a related row shows its verdict as a dot");
  assert.match(dot.title, /^statement deviation, superseded/);
  assert.equal(el(documentRef, "provenance-note").textContent,
               "Curation and pipeline links and machine verdicts, partly reviewed.");
  await fire(documentRef, "keydown", { key: "Escape" });
  assert.equal(el(documentRef, "index-footer").textContent, "Machine verdicts, unreviewed.");
  assert.deepEqual(el(documentRef, "verdict-legend").children.map(textOf), ["fixed"]);
});

test("item: a status states what applies, with its class and its note", async () => {
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0002");
  assert.deepEqual(el(documentRef, "related-heading").children.map(textOf), ["Related", " · support"],
                   "one role for every related card is said once, in the heading");
  assert.deepEqual(rows(documentRef, "related-list").map(rowKind), ["theorem"]);
  assert.equal(itemTitle(documentRef), "Definition 2.1");
  const pill = el(documentRef, "target-summary");
  assert.deepEqual([pill.textContent, pill.title, pill.className],
                   ["Support only", "Supported only by declarations that state a neighboring item.",
                    "target-summary coverage-support_only"]);
  assert.equal(el(documentRef, "coverage-note").hidden, false);
  assert.equal(el(documentRef, "coverage-note").textContent,
               "The companion states the neighbouring box lemma, not this definition.");
  assert.deepEqual(el(documentRef, "key-note").children.map(textOf), ["No key declaration states this passage."]);
  assert.equal(el(documentRef, "key-list").hidden, true);
  assert.deepEqual(rows(documentRef, "related-list").map((row) => row.children[0].title.split("\n")[0]),
                   ["Notes.Set.inter_sub"]);
  assert.equal(polygonOf(documentRef, "loc:0002").dataset.coverage, "partial");
});

test("item: an item that links nothing says so and names its cluster", async () => {
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0003");
  assert.equal(itemTitle(documentRef), "Exercise 3.1");
  assert.deepEqual([el(documentRef, "target-summary").textContent, el(documentRef, "target-summary").hidden], ["", true]);
  assert.deepEqual(el(documentRef, "key-note").children.map(textOf),
                   ["No Lean declaration is linked to this passage.", "Ledger cluster cluster:sets"]);
  assert.equal(el(documentRef, "provenance-note").hidden, true);
  assert.deepEqual([el(documentRef, "key-list").hidden, el(documentRef, "key-count").hidden], [true, true]);
  assert.equal(el(documentRef, "related-section").hidden, true);
  assert.equal(polygonOf(documentRef, "loc:0003").dataset.coverage, "quiet");
});

const MINIMAL = { display: { minimal: true, strip_prefix: "Notes." } };

test("minimal: the passage, its links and their statements stay; the metadata goes", async () => {
  const { documentRef, result } = await start({ config: makeConfig(MINIMAL) });
  assert.equal(el(documentRef, "status").textContent, "Verified PDF", "still read by a screen reader");
  assert.ok(el(documentRef, "status-chip").classList.contains("visually-hidden"), "but out of sight");
  assert.equal(el(documentRef, "keys").hidden, true, "the toolbar buttons name their shortcuts");
  await selectPageRegion(documentRef, result.state, "loc:0001");
  assert.equal(el(documentRef, "target-summary").hidden, true, "the cards say it is formalized");
  const [[eyebrow, name, pre]] = keyCards(documentRef);
  assert.equal(textOf(eyebrow.children[0]), "", "no kind and no badge");
  assert.deepEqual([eyebrow.children[1].tagName, eyebrow.children[1].attributes.href],
                   ["A", `${ORIGIN}/Notes/Box/index.html#Notes___Box___volume_nonneg`], "the link stays");
  assert.equal(textOf(name), "Box.volume_nonneg");
  assert.equal(textOf(pre), "theorem volume_nonneg (b : Box) : 0 <= b.volume", "and the statement");
  assert.deepEqual(rows(documentRef, "related-list").map((row) => [rowName(row), rowKind(row)]), [
    ["Box.volume_mono", "module page"], ["Set.inter_sub", ""], ["example:0123456789ab", "no page"],
    ["Set.union_comm", ""],
  ], "related rows drop their role and kind, never where the link goes");
  assert.equal(rows(documentRef, "related-list")[0].children[0].attributes.href, `${ORIGIN}/Notes/Box/index.html`);
  assert.equal(el(documentRef, "related-count").hidden, true);
  assert.equal(el(documentRef, "provenance-note").hidden, true, "how the links were curated is left out");
  await selectPageRegion(documentRef, result.state, "loc:0004");
  const [[privateEyebrow]] = keyCards(documentRef);
  assert.equal(textOf(privateEyebrow.children[0]), "", "a key card's badges go too");
});

test("minimal: several key cards need no count", async () => {
  const sections = makeSections(PDF_SHA256);
  const key = linkRecord(sections, "item:theorem-1.1", "key", "Notes.Box.volume_nonneg");
  sections[1].push(relinked(key, "Notes.Box.volume_pos"));
  const plain = await start({ sections });
  await selectPageRegion(plain.documentRef, plain.result.state, "loc:0001");
  assert.equal(el(plain.documentRef, "key-count").hidden, false, "the default overlay counts them");
  const { documentRef, result } = await start({ config: makeConfig(MINIMAL), sections });
  await selectPageRegion(documentRef, result.state, "loc:0001");
  assert.equal(keyCards(documentRef).length, 2);
  assert.deepEqual([el(documentRef, "key-count").hidden, el(documentRef, "key-head").hidden], [true, true]);
});

test("minimal: a status that qualifies the links still says so, with its note", async () => {
  const { documentRef, result } = await start({ config: makeConfig(MINIMAL) });
  await selectPageRegion(documentRef, result.state, "loc:0002");
  const pill = el(documentRef, "target-summary");
  assert.deepEqual([pill.textContent, pill.hidden], ["Support only", false]);
  assert.equal(el(documentRef, "coverage-note").hidden, false);
  assert.deepEqual(el(documentRef, "related-heading").children.map(textOf), ["Related", " · support"],
                   "the heading still says what the related rows are");
});

test("minimal: an item that links nothing says so, without its ledger cluster", async () => {
  const { documentRef, result } = await start({ config: makeConfig(MINIMAL) });
  await selectPageRegion(documentRef, result.state, "loc:0003");
  assert.deepEqual(el(documentRef, "key-note").children.map(textOf), ["No Lean declaration is linked to this passage."]);
});

test("minimal: a long list has no filter or count, and every card stays reachable", async () => {
  const sections = makeSections(PDF_SHA256);
  const key = linkRecord(sections, "item:theorem-1.1", "key", "Notes.Box.volume_nonneg");
  for (let n = 0; n < 30; n += 1) {
    const name = `Notes.Fill.f${String(n).padStart(2, "0")}`;
    sections[1].push(relinked(key, name, { drop: ["snippet"], kind: "def", role: "attributed" }));
  }
  const { documentRef, result } = await start({ config: makeConfig(MINIMAL), sections });
  await selectPageRegion(documentRef, result.state, "loc:0001");
  assert.equal(el(documentRef, "target-filter").hidden, true);
  assert.equal(el(documentRef, "key-head").hidden, true);
  assert.equal(el(documentRef, "related-pager").hidden, false);
  assert.equal(el(documentRef, "target-page").textContent, "1–25 of 34");
  await fire(el(documentRef, "next-targets"), "click");
  assert.equal(el(documentRef, "target-page").textContent, "26–34 of 34");
});

test("minimal: a record that cannot be placed is still reported", async () => {
  const sections = makeSections(PDF_SHA256);
  sections[2][0].subject[0].location = { page: 5, rect: [0.9, 0.1, 0.4, 0.1] };
  const { documentRef } = await start({ config: makeConfig(MINIMAL), sections });
  assert.equal(el(documentRef, "notice").hidden, false);
});

test("minimal: the faithfulness switch and the verdicts still work", async () => {
  const { documentRef, result } = await start({ config: makeConfig(MINIMAL) });
  const button = el(documentRef, "toggle-faithfulness");
  await selectPageRegion(documentRef, result.state, "loc:0001");
  assert.equal(button.hidden, false);
  assert.equal(el(documentRef, "provenance-note").hidden, true, "no footer while no verdict is shown");
  await fire(button, "click");
  assert.equal(el(documentRef, "verdict-notes").children.length, 1);
  assert.deepEqual(verdictMarks(documentRef), ["faithful|card-verdict verdict-badge verdict-faithful"]);
  assert.equal(el(documentRef, "provenance-note").hidden, false, "shown verdicts keep their caveat");
  assert.match(el(documentRef, "provenance-note").textContent, /^Machine verdicts, (partly reviewed|unreviewed)\.$/);
});

test("minimal: a PDF that is not verified keeps its status in sight", async () => {
  const { documentRef } = await start({ config: makeConfig(MINIMAL), sections: makeSections(fakeSha256("9")) });
  assert.equal(el(documentRef, "status").textContent, "PDF not verified");
  assert.equal(el(documentRef, "status-chip").classList.contains("visually-hidden"), false);
  assert.equal(el(documentRef, "error").hidden, false);
});

test("item: a region opens the item its curation names first, and never an empty one first", async () => {
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0005");
  const choices = el(documentRef, "item-choices");
  assert.deepEqual(choices.children.map((button) => button.textContent), ["Remark 7.1 and equation (7)", "Equation 7"]);
  assert.equal(itemTitle(documentRef), "Remark 7.1 and equation (7)", "a parenthetical with no letter stays");
  const sections = makeSections(PDF_SHA256);
  for (const end of sections[2].flatMap((record) => record.subject)) {
    const ext = end.extensions?.[NAMESPACE];
    if (ext?.region === "loc:0005") {
      ext.region_items = ["item:equation-7", "item:remark-7.1"];
    }
  }
  const second = await start({ sections });
  await selectPageRegion(second.documentRef, second.result.state, "loc:0005");
  assert.deepEqual(el(second.documentRef, "item-choices").children.map((button) => button.textContent),
                   ["Equation 7", "Remark 7.1 and equation (7)"]);
  assert.equal(itemTitle(second.documentRef), "Remark 7.1 and equation (7)",
               "the equation has no card, so the remark opens");
  assert.equal(textOf(keyCards(second.documentRef)[0][1]), "Box.volume_nonneg");
});

test("item: a region with two items offers a choice, and the choice switches the cards", async () => {
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0004");
  const choices = el(documentRef, "item-choices");
  assert.equal(choices.hidden, false);
  assert.deepEqual(choices.children.map((button) => button.textContent), ["A remark on unions", "Theorem 1.1"]);
  assert.equal(itemTitle(documentRef), "A remark on unions");
  const [[eyebrow, name]] = keyCards(documentRef);
  assert.equal(name.title, "Notes.Internal.aux");
  assert.deepEqual(eyebrow.children[0].children.map(textOf), ["lemma", "private"]);
  await fire(choices.children[1], "click");
  assert.equal(itemTitle(documentRef), "Theorem 1.1");
  assert.equal(rows(documentRef, "related-list").length, 4);
  assert.deepEqual(choices.children.map((button) => button.attributes["aria-pressed"]), ["false", "true"]);
});

test("item: the strip steps through the page's passages and returns to the page", async () => {
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0001");
  assert.equal(el(documentRef, "back-to-page").textContent, "← Page 2");
  assert.equal(el(documentRef, "item-position").textContent, "1 of 2");
  assert.deepEqual([el(documentRef, "previous-item").disabled, el(documentRef, "next-item").disabled], [true, false]);
  await fire(el(documentRef, "next-item"), "click");
  assert.equal(itemTitle(documentRef), "A remark on unions");
  assert.equal(result.state.region.id, "loc:0004");
  assert.equal(el(documentRef, "item-position").textContent, "2 of 2");
  assert.equal(el(documentRef, "next-item").disabled, true);
  await fire(el(documentRef, "back-to-page"), "click");
  assert.equal(result.state.region, null);
  assert.equal(el(documentRef, "selection").hidden, true);
  assert.equal(el(documentRef, "region-bar").hidden, true);
});

test("item: past twenty cards a filter narrows both lists, and the related rows page by 25", async () => {
  const sections = makeSections(PDF_SHA256);
  const key = linkRecord(sections, "item:theorem-1.1", "key", "Notes.Box.volume_nonneg");
  for (let n = 0; n < 30; n += 1) {
    const name = `Notes.Fill.f${String(n).padStart(2, "0")}`;
    sections[1].push(relinked(key, name, { drop: ["snippet"], kind: "def", role: "attributed" }));
  }
  const { documentRef, result } = await start({ sections });
  await selectPageRegion(documentRef, result.state, "loc:0001");
  const filter = el(documentRef, "target-filter");
  assert.equal(filter.hidden, false);
  assert.equal(el(documentRef, "key-head").hidden, false, "one key card, but the filter's line shows");
  assert.match(SHELL, /<div id="key-head" class="key-head" hidden>\s*<p id="key-count"[^>]*><\/p>\s*<input\s+id="target-filter"/,
               "the filter sits beside the key count, above the key cards it narrows");
  assert.doesNotMatch(SHELL.slice(SHELL.indexOf('id="related-section"')), /id="target-filter"|id="filter-empty"/,
                      "not in the Related head, after every key card");
  assert.equal(el(documentRef, "related-count").textContent, "34");
  assert.deepEqual(el(documentRef, "related-heading").children.map(textOf), ["Related", " · attributed"],
                   "the role most rows share is said once");
  assert.deepEqual(rows(documentRef, "related-list").slice(0, 2).map(rowKind), ["theorem · module page", "def"]);
  assert.equal(rows(documentRef, "related-list").length, 25);
  assert.equal(el(documentRef, "related-pager").hidden, false);
  assert.equal(el(documentRef, "target-page").textContent, "1–25 of 34");
  await fire(el(documentRef, "next-targets"), "click");
  assert.equal(el(documentRef, "target-page").textContent, "26–34 of 34");
  filter.value = "f2";
  await fire(filter, "input");
  assert.equal(el(documentRef, "related-count").textContent, "10");
  assert.equal(el(documentRef, "related-pager").hidden, true);
  assert.deepEqual(rows(documentRef, "related-list").slice(0, 2).map(rowName), ["Fill.f20", "Fill.f21"]);
  assert.equal(el(documentRef, "filter-empty").hidden, true);
  filter.value = "nothing has this name";
  await fire(filter, "input");
  assert.equal(el(documentRef, "filter-empty").hidden, false, "an empty match says so");
  assert.equal(el(documentRef, "related-count").textContent, "0");
});

test("item: a related row whose role and kind are one word says it once", async () => {
  const sections = makeSections(PDF_SHA256);
  const mono = linkRecord(sections, "item:theorem-1.1", "attributed", "Notes.Box.volume_mono");
  const copy = (role, name, kind) => relinked(mono, name, { drop: ["declaration_verdict"], kind, role });
  for (let n = 0; n < 4; n += 1) {
    sections[1].push(copy("attributed", `Notes.Box.a${n}`, "theorem"));
  }
  sections[1].push(copy("example", "Notes.Box.ex", "example"));
  const { documentRef, result } = await start({ sections });
  await selectPageRegion(documentRef, result.state, "loc:0001");
  assert.deepEqual(el(documentRef, "related-heading").children.map(textOf), ["Related", " · attributed"]);
  const row = rows(documentRef, "related-list").find((li) => rowName(li) === "Box.ex");
  assert.equal(rowKind(row), "example · module page");
});

test("focus: leaving the item view puts focus on the row of the item that was open", async () => {
  const documentRef = fakeDocument(SHELL);
  const focused = [];
  const create = documentRef.createElement;
  const focusable = (node, name) => Object.assign(node, { focus: () => focused.push(name(node)) });
  documentRef.createElement = (tag) => focusable(create(tag), (node) => `${node.tagName}:${node.dataset.itemId ?? ""}`);
  for (const id of ["item-label", "index-heading", "previous-item", "next-item"]) {
    focusable(el(documentRef, id), () => id);
  }
  const { result } = await start({ documentRef });
  await turnTo(documentRef, 2);
  await fire(indexRows(documentRef)[1], "click");
  assert.deepEqual(focused.splice(0), ["item-label"], "opening a row moves focus to the item's heading");
  await fire(el(documentRef, "back-to-page"), "click");
  assert.deepEqual(focused.splice(0), [`BUTTON:${result.state.index.pages.get(2)[1].itemIds[0]}`]);
  await selectPageRegion(documentRef, result.state, "loc:0001");
  await fire(el(documentRef, "next-item"), "click");
  assert.equal(el(documentRef, "next-item").disabled, true);
  assert.deepEqual(focused.splice(0), ["previous-item"], "a stepper button that turns disabled hands focus on");
});

// --- navigation and the remembered page -------------------------------------

test("navigation: the buttons and the page input move, and clear the selection", async () => {
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0001");
  assert.equal(el(documentRef, "selection").hidden, false);
  await fire(el(documentRef, "next-page"), "click");
  assert.equal(result.state.currentPage, 3);
  assert.equal(el(documentRef, "selection").hidden, true, "a page turn clears the selection");
  assert.equal(el(documentRef, "page-number").value, "3");
  await fire(el(documentRef, "previous-page"), "click");
  assert.equal(result.state.currentPage, 2);
  const input = el(documentRef, "page-number");
  input.value = "999";
  await fire(input, "change");
  assert.equal(result.state.currentPage, 12, "the page is clamped to the document");
  assert.equal(el(documentRef, "next-page").disabled, true);
});

test("navigation: the page is remembered under the configured storage key", async () => {
  const storage = memoryStorage();
  const { hashes } = await start({ storage });
  assert.equal(storage.getItem("notes-overlay-page"), "1");
  assert.deepEqual(hashes, ["#page=1"]);
  const resumed = await start({ storage: memoryStorage({ "notes-overlay-page": "7" }) });
  assert.equal(resumed.result.state.currentPage, 7);
  const hashed = await start({ locationRef: { hash: "#page=4" }, storage: memoryStorage({ "notes-overlay-page": "7" }) });
  assert.equal(hashed.result.state.currentPage, 4);
  assert.equal(rememberedPage("k", { locationRef: { hash: "#page=3" }, storage: null }), 3);
  assert.equal(rememberedPage("k", { locationRef: { hash: "#x" }, storage: memoryStorage() }), null);
});

test("navigation: an edited #page= hash changes the page", async () => {
  const listeners = {};
  const windowRef = { addEventListener: (type, listener) => { listeners[type] = listener; } };
  const locationRef = { hash: "" };
  const { hashes, result } = await start({ locationRef, windowRef });
  locationRef.hash = "#page=5";
  listeners.hashchange();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(result.state.currentPage, 5);
  assert.equal(hashes.at(-1), "#page=5");
  locationRef.hash = "#elsewhere";
  listeners.hashchange();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(result.state.currentPage, 5, "a hash that names no page changes nothing");
});

test("navigation: a storage that throws on every access never breaks the viewer", async () => {
  const storage = throwingStorage();
  assert.equal(rememberedPage("notes-overlay-page", { locationRef: { hash: "" }, storage }), null);
  const { documentRef, result } = await start({ storage });
  assert.equal(result.state.currentPage, 1);
  await fire(el(documentRef, "next-page"), "click");
  assert.equal(result.state.currentPage, 2, "writing the remembered page is best-effort");
  assert.equal(el(documentRef, "error").hidden, true);
});

// --- the keyboard -----------------------------------------------------------

test("keyboard: the shortcuts work bare and are ignored with a modifier or in an input", async () => {
  const { documentRef, result } = await start();
  await fire(documentRef, "keydown", { key: "ArrowRight" });
  assert.equal(result.state.currentPage, 2);
  await fire(documentRef, "keydown", { key: "ArrowLeft" });
  assert.equal(result.state.currentPage, 1);
  await fire(documentRef, "keydown", { key: "+" });
  assert.equal(result.state.zoom, 110);
  await fire(documentRef, "keydown", { key: "-" });
  assert.equal(result.state.zoom, 100);
  await fire(documentRef, "keydown", { key: "+", shiftKey: true });
  assert.equal(result.state.zoom, 110, "Shift is how a US keyboard types +");
  await fire(documentRef, "keydown", { key: "-" });
  await fire(documentRef, "keydown", { key: "r" });
  assert.equal(result.state.rotation, 90);
  for (const modifier of ["ctrlKey", "metaKey", "altKey", "shiftKey"]) {
    await fire(documentRef, "keydown", { [modifier]: true, key: "ArrowRight" });
    assert.equal(result.state.currentPage, 1, `${modifier}+ArrowRight must not turn the page`);
  }
  for (const tagName of ["INPUT", "SELECT", "TEXTAREA"]) {
    await fire(documentRef, "keydown", { key: "ArrowRight", target: { tagName } });
  }
  assert.equal(result.state.currentPage, 1);
});

test("keyboard: every shortcut a button advertises does what the button does", async () => {
  const advertised = [...SHELL.matchAll(/<button\b[^>]*>/g)]
    .map(([tag]) => [tag.match(/\sid="([^"]+)"/)?.[1], tag.match(/\saria-keyshortcuts="([^"]+)"/)?.[1],
                     tag.match(/\stitle="[^"]*\(([^)]+)\)"/)?.[1]])
    .filter(([id, keys]) => id && keys);
  assert.deepEqual(advertised.map(([id, , shown]) => [id, shown]),
                   [["previous-page", "←"], ["next-page", "→"], ["zoom-out", "−"], ["zoom-in", "+"],
                    ["rotate-right", "R"]], "each toolbar button names its key in its tooltip");
  const snapshot = (state) => [state.currentPage, state.zoom, state.rotation];
  for (const [id, keys] of advertised) {
    const clicked = await start({ locationRef: { hash: "#page=5" } });
    await fire(el(clicked.documentRef, id), "click");
    assert.notDeepEqual(snapshot(clicked.result.state), [5, 100, 0], `#${id} does something`);
    for (const token of keys.split(" ")) {
      const key = token === "Plus" ? "+" : token;
      const pressed = await start({ locationRef: { hash: "#page=5" } });
      await fire(pressed.documentRef, "keydown", { key, shiftKey: key === "+" || /^[A-Z]$/.test(key) });
      assert.deepEqual(snapshot(pressed.result.state), snapshot(clicked.result.state), `${token} on #${id}`);
    }
  }
});

test("keyboard: the shortcut test is the predicate's own, so both halves agree", () => {
  assert.equal(isViewerShortcut({ key: "ArrowRight", target: {} }), true);
  assert.equal(isViewerShortcut({ ctrlKey: true, key: "ArrowRight", target: {} }), false);
  assert.equal(isViewerShortcut({ key: "ArrowRight", metaKey: true, target: {} }), false);
  assert.equal(isViewerShortcut({ key: "r", target: { tagName: "INPUT" } }), false);
  assert.equal(isViewerShortcut({ key: "+", shiftKey: true, target: {} }), true, "a shifted character counts");
  assert.equal(isViewerShortcut({ key: "ArrowRight", shiftKey: true, target: {} }), false,
               "a shifted named key does not");
  assert.equal(isViewerShortcut(null), false);
});

test("escape clears the selected region and Enter on a region selects it", async () => {
  const { documentRef, result } = await start();
  const region = await selectPageRegion(documentRef, result.state, "loc:0001");
  assert.equal(result.state.region.id, region.id);
  await fire(documentRef, "keydown", { key: "Escape" });
  assert.equal(result.state.region, null);
  assert.equal(el(documentRef, "selection").hidden, true);
  const polygon = el(documentRef, "overlay").children.find((child) => child.dataset.regionId === region.id);
  await fire(polygon, "keydown", { key: "Enter" });
  assert.equal(result.state.region.id, region.id);
  assert.equal(polygon.attributes["aria-pressed"], "true");
});

// --- the shell --------------------------------------------------------------

test("shell: index.html is generic, keeps its CSP and has no file picker", async () => {
  assert.match(SHELL, /default-src 'self'/);
  assert.match(SHELL, /script-src 'self'/);
  assert.match(SHELL, /style-src 'self'/);
  assert.match(SHELL, /connect-src 'self'/);
  assert.match(SHELL, /object-src 'none'/);
  assert.doesNotMatch(SHELL, /type=["']file|Choose PDF|pdf-file/);
  assert.doesNotMatch(SHELL, /<script(?![^>]*\ssrc=)/, "no inline script");
  assert.doesNotMatch(SHELL, /<style|\sstyle=/, "no inline style");
  for (const gone of ["Verified loopback PDF", "Source item", "keyboard-help", "role-filter", "rotate-left",
                      'id="zoom"', "Find declaration"]) {
    assert.ok(!SHELL.includes(gone), `the shell still has ${gone}`);
  }
  const { readFile } = await import("node:fs/promises");
  const { VIEWER_DIR } = await import("./fixtures.mjs");
  const path = await import("node:path");
  const sources = await Promise.all(["viewer.js", "viewer-core.js"].map(
    (name) => readFile(path.join(VIEWER_DIR, name), "utf8")));
  for (const source of [SHELL, ...sources]) {
    assert.doesNotMatch(source.replaceAll("http://www.w3.org/2000/svg", ""), /https?:\/\//);
    assert.doesNotMatch(source, /\.innerHTML\s*=|outerHTML|insertAdjacentHTML|document\.write\(/);
  }
});

test("shell: a back link the config spells as javascript: never reaches the header", async () => {
  const { documentRef } = await start({ config: makeConfig({ back_link: { href: "javascript:alert(1)", text: "Back" } }) });
  assert.deepEqual([el(documentRef, "back-link").hidden, el(documentRef, "back-link").attributes.href], [true, undefined]);
  const offsite = await start({ config: makeConfig({ back_link: { href: "//notes.example.invalid/x.html", text: "Back" } }) });
  assert.deepEqual([el(offsite.documentRef, "back-link").hidden, el(offsite.documentRef, "back-link").attributes.href],
                   [true, undefined]);
});

test("shell: with no back link in the config the top bar starts with the title", async () => {
  assert.match(SHELL, /<a id="back-link" class="back-link" hidden><\/a>/, "the shell names no link of its own");
  const { documentRef } = await start({ config: makeConfig({ back_link: undefined }) });
  assert.deepEqual([el(documentRef, "back-link").hidden, el(documentRef, "back-link").attributes.href,
                    el(documentRef, "back-link").textContent], [true, undefined, ""]);
  const css = await (await import("node:fs/promises")).readFile(
    (await import("node:path")).join((await import("./fixtures.mjs")).VIEWER_DIR, "viewer.css"), "utf8");
  assert.match(css, /\.back-link\[hidden\] \+ \.topbar-sep \{\s*display: none;/, "and no divider");
});

test("shell: the title and heading are placeholders the configuration replaces", async () => {
  const placeholder = /Source and formalization overlay/;
  assert.match(SHELL, placeholder);
  const { documentRef } = await start({ config: makeConfig({ lede: "", title: "Another title" }) });
  assert.equal(documentRef.title, "Another title");
  assert.equal(el(documentRef, "page-heading").textContent, "Another title");
  assert.equal(el(documentRef, "page-lede").hidden, true);
});
