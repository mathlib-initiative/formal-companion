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
import { HOVER_LINGER, bootstrapViewer, isViewerShortcut, rememberedPage } from "../runtime/viewer/viewer.js";
import {
  BUNDLE_PATH,
  BUNDLE_URL,
  ORIGIN,
  PAGE_TEXT,
  PAGE_URL,
  PDF_BYTES,
  PDF_URL,
  PROJECT_ORIGIN,
  PROJECT_PAGE_URL,
  VERSO_TARGET,
  VERSO_URL,
  fakeDocument,
  fakePdfjs,
  fakeSha256,
  fire,
  jsonResponse,
  makeConfig,
  bundleText,
  makeGenerated,
  linkRecord,
  makeProofSections,
  makeSections,
  makeVerso,
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
  pageUrl = PAGE_URL,
  bundleUrl = BUNDLE_URL,
  pdfUrl = PDF_URL,
  pdf = () => pdfResponse(),
  pdfjs = fakePdfjs(),
  served = undefined,
  storage = memoryStorage(),
  verso = undefined,
  windowRef = undefined,
  renderMath = undefined,
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
    if (url === pdfUrl) {
      return pdf();
    }
    if (url === bundleUrl) {
      return jsonResponse(served ?? bundleText(sections));
    }
    if (url === VERSO_URL) {
      return verso === undefined ? notFound() : jsonResponse(verso);
    }
    throw new Error(`the viewer asked for ${url}`);
  };
  const result = await bootstrapViewer({
    cryptoProvider: globalThis.crypto,
    documentRef,
    fetchImpl,
    historyRef,
    locationRef,
    pageUrl,
    pdfjsLoader: async () => pdfjs,
    storage,
    windowRef,
    searchDelay: 0,
    ...(renderMath ? { renderMath } : {}),
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

test("start-up: the config, GENERATED.json, the one bundle, verso.json and the route are fetched", async () => {
  const { fetched, pdfjs } = await start();
  assert.deepEqual(fetched.map((call) => call.url),
                   ["../overlay.config.json", "../GENERATED.json", BUNDLE_URL, VERSO_URL, PDF_URL]);
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

test("pdf: a published PDF is fetched from beside the config, under a project path too", async () => {
  const site = `${PROJECT_ORIGIN}/notes-companion/pdf-overlay`;
  const config = makeConfig({ pdf: { file_name: "notes.pdf", publish: { license: "CC-BY-4.0" } } });
  const { documentRef, fetched, pdfjs } = await start({
    bundleUrl: `${site}/${BUNDLE_PATH}`, config, pageUrl: PROJECT_PAGE_URL, pdfUrl: `${site}/notes.pdf`,
  });
  assert.deepEqual(fetched.map((call) => call.url),
                   ["../overlay.config.json", "../GENERATED.json", `${site}/${BUNDLE_PATH}`, VERSO_URL,
                    `${site}/notes.pdf`]);
  assert.deepEqual(fetched.at(-1).options, { cache: "no-store", credentials: "same-origin" });
  assert.deepEqual(pdfjs.calls[0].data, PDF_BYTES);
  assert.equal(el(documentRef, "status").textContent, "Verified PDF");
  assert.match(el(documentRef, "status-note").textContent, /^The PDF is published with this site under CC-BY-4\.0\./);
});

test("pdf: a published PDF is verified as a loopback one is, and named as published", async () => {
  const config = makeConfig({ pdf: { file_name: "notes.pdf", publish: { license: "MIT" } } });
  const pdfjs = fakePdfjs();
  const { documentRef } = await start({ config, pdfjs, pdfUrl: `${ORIGIN}/pdf-overlay/notes.pdf`,
                                        sections: makeSections(fakeSha256("9")) });
  assert.equal(pdfjs.calls.length, 0, "PDF.js must not see unverified bytes");
  assert.match(el(documentRef, "error").textContent, /^Published PDF SHA-256 is/);
  assert.equal(el(documentRef, "status").textContent, "PDF not verified");
});

test("pdf: with no pdf.publish the shell's note that the PDF stays on loopback is left alone", async () => {
  const documentRef = fakeDocument(SHELL);
  el(documentRef, "status-note").textContent = "The shell's own note.";
  await start({ documentRef });
  assert.equal(el(documentRef, "status-note").textContent, "The shell's own note.");
  assert.match(SHELL, /<p id="status-note" class="status-pop">\s*The PDF is served only on loopback/);
});

// --- the text layer ---------------------------------------------------------

test("text: a rendered page carries PDF.js's text layer, in the viewer's own container", async () => {
  const pdfjs = fakePdfjs();
  const { documentRef } = await start({ pdfjs });
  const layer = el(documentRef, "text-layer");
  assert.deepEqual(layer.children.map((span) => span.textContent), PAGE_TEXT);
  assert.equal(pdfjs.textLayers.length, 1);
  const [{ options }] = pdfjs.textLayers;
  assert.equal(options.container, layer, "the layer fills the shell's own div, under the boxes");
  assert.equal(options.viewport.scale, 1);
  assert.equal(layer.style["--total-scale-factor"], "1", "the layer is sized through CSSOM");
  assert.deepEqual(pdfjs.streams, [{ disableNormalization: true }], "the text is copied as the PDF spells it");
});

test("text: a page turn, a zoom and a rotation each cancel the layer they leave and start another", async () => {
  const pdfjs = fakePdfjs();
  const { documentRef } = await start({ pdfjs });
  const layer = el(documentRef, "text-layer");
  await fire(el(documentRef, "next-page"), "click");
  assert.equal(pdfjs.textLayers.length, 2);
  assert.equal(pdfjs.textLayers[0].cancelled, 1, "the page being left stops streaming");
  assert.deepEqual(layer.children.map((span) => span.textContent), PAGE_TEXT, "and its spans are gone");
  await fire(el(documentRef, "zoom-in"), "click");
  assert.equal(pdfjs.textLayers[2].options.viewport.scale, 1.1);
  assert.equal(layer.style["--total-scale-factor"], "1.1");
  await fire(el(documentRef, "rotate-right"), "click");
  assert.equal(pdfjs.textLayers[3].options.viewport.rotation, 90);
  assert.deepEqual(pdfjs.textLayers.map((one) => one.cancelled), [1, 1, 1, 0]);
});

test("text: a layer that fails, or a build without one, leaves a page that still renders", async () => {
  for (const pdfjs of [fakePdfjs({ textLayerError: "no text" }), fakePdfjs({ textLayer: false })]) {
    const { documentRef, result } = await start({ pdfjs });
    assert.equal(el(documentRef, "error").hidden, true);
    assert.equal(el(documentRef, "status").textContent, "Verified PDF");
    assert.equal(el(documentRef, "text-layer").children.length, 0);
    await fire(el(documentRef, "next-page"), "click");
    assert.equal(result.state.currentPage, 2, "and the page still turns");
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
  const lines = (count) => Array.from({ length: count }, (_, n) => `  h${n} : a = a`).join("\n");
  assert.deepEqual(await statement(`theorem long : ${"a + ".repeat(400)}a = a`), ["lean is-clamped", "Show all"],
                   "one 1600-character line wraps past twenty lines");
  assert.deepEqual(await statement(lines(14)), ["lean", undefined], "fourteen short lines fit");
  assert.deepEqual(await statement(lines(20)), ["lean", undefined],
                   "a definition a few lines longer is shown whole, not clamped to hide two or three lines");
  assert.deepEqual(await statement(lines(21)), ["lean is-clamped", "Show all"]);
  assert.deepEqual(await statement(`structure Long where\n${lines(59)}\n  …`), ["lean is-clamped", "Show all"],
                   "a long definition is clamped to fourteen lines");
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

test("minimal: a status that qualifies the links still says so, and its note waits behind the control", async () => {
  const { documentRef, result } = await start({ config: makeConfig(MINIMAL) });
  await selectPageRegion(documentRef, result.state, "loc:0002");
  const pill = el(documentRef, "target-summary");
  assert.deepEqual([pill.textContent, pill.hidden], ["Support only", false]);
  assert.equal(el(documentRef, "coverage-note").hidden, true);
  assert.equal(el(documentRef, "note-toggle").hidden, false);
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

// --- reading without the annotations ----------------------------------------

test("reading: hide every annotation surface and restore the selected passage without reloading", async () => {
  const { documentRef, fetched, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0001");
  await fire(el(documentRef, "zoom-in"), "click");
  await fire(el(documentRef, "rotate-right"), "click");
  const { currentPage, zoom, rotation, selected, region, renderEpoch } = result.state;
  const cards = [...el(documentRef, "key-list").children];
  const requests = fetched.length;
  const toggle = el(documentRef, "toggle-reading");
  assert.equal(toggle.disabled, false);
  await fire(toggle, "click");
  assert.equal(result.state.readingMode, true);
  assert.equal(el(documentRef, "workspace").classList.contains("is-reading"), true);
  assert.equal(el(documentRef, "details").hidden, true);
  assert.equal(el(documentRef, "overlay").attributes.hidden, "");
  for (const id of ["region-bar", "region-peek", "hover-card"]) assert.equal(el(documentRef, id).hidden, true, id);
  assert.equal(toggle.textContent, "Show links");
  assert.equal(toggle.attributes["aria-expanded"], "false");
  assert.equal(el(documentRef, "page-scroller").attributes["aria-label"], "Rendered PDF page");
  await fire(toggle, "click");
  assert.equal(result.state.readingMode, false);
  assert.equal(el(documentRef, "workspace").classList.contains("is-reading"), false);
  assert.equal(el(documentRef, "details").hidden, false);
  assert.equal(el(documentRef, "overlay").attributes.hidden, undefined);
  assert.equal(el(documentRef, "region-bar").hidden, false);
  assert.equal(toggle.textContent, "Read PDF");
  assert.equal(toggle.attributes["aria-expanded"], "true");
  assert.deepEqual([result.state.currentPage, result.state.zoom, result.state.rotation], [currentPage, zoom, rotation]);
  assert.equal(result.state.selected, selected);
  assert.equal(result.state.region, region);
  assert.deepEqual(el(documentRef, "key-list").children, cards);
  assert.equal(result.state.renderEpoch, renderEpoch, "a mode switch does not redraw the PDF");
  assert.equal(fetched.length, requests, "a mode switch makes no requests");
});

test("reading: page, zoom and rotation controls work while annotations stay hidden", async () => {
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0001");
  await fire(el(documentRef, "toggle-reading"), "click");
  for (const id of ["zoom-in", "rotate-right", "next-page"]) {
    await fire(el(documentRef, id), "click");
    assert.equal(el(documentRef, "details").hidden, true);
    assert.equal(el(documentRef, "overlay").attributes.hidden, "");
    assert.equal(el(documentRef, "region-bar").hidden, true);
  }
  assert.deepEqual([result.state.currentPage, result.state.zoom, result.state.rotation], [3, 110, 90]);
  assert.equal(el(documentRef, "text-layer").hidden, false, "the PDF text remains selectable");
  await fire(el(documentRef, "toggle-reading"), "click");
  assert.equal(result.state.currentPage, 3);
  assert.equal(el(documentRef, "overlay").attributes.hidden, undefined);
});

test("reading: an open code hover closes, and Escape restores the links and selected passage", async () => {
  const { card, documentRef, named, result } = await versoCard();
  await fire(named("b"), "pointerenter", { pointerType: "mouse" });
  await fire(named("b"), "pointerleave", { pointerType: "mouse" });
  assert.equal(card.hidden, false);
  const selected = result.state.selected;
  await fire(el(documentRef, "toggle-reading"), "click");
  await linger();
  assert.equal(card.hidden, true);
  await fire(documentRef, "keydown", { key: "Escape" });
  assert.equal(result.state.readingMode, false);
  assert.equal(el(documentRef, "details").hidden, false);
  assert.equal(result.state.selected, selected);
});

test("reading: the control stays disabled when the PDF cannot be verified", async () => {
  const { documentRef } = await start({ pdf: () => pdfResponse(new Uint8Array([1])) });
  assert.equal(el(documentRef, "error").hidden, false);
  assert.equal(el(documentRef, "toggle-reading").disabled, true);
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
    .filter(([id, keys]) => id && keys && !keys.includes("Meta+"));
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
  assert.match(SHELL, /content="default-src 'self'; script-src 'self'; worker-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'"/,
               "the CSP is exactly as it was");
  assert.match(SHELL, /<div id="hover-card" class="hover-card" role="tooltip" hidden><\/div>/, "one hover card, empty");
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
    assert.doesNotMatch(source, /\.innerHTML\s*=|outerHTML|insertAdjacentHTML|document\.write\(|DOMParser|createContextualFragment/);
    assert.doesNotMatch(source, /setAttribute\(\s*["']style|\.cssText|\beval\(|new Function/);
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

// --- Verso statements and hovers ----------------------------------------------

const STATEMENT = "theorem volume_nonneg (b : Box) : 0 <= b.volume";

/** Every element in a statement that carries a token, in reading order. */
function tokensOf(node) {
  const found = [];
  const walk = (child) => {
    if (typeof child !== "object" || !child) {
      return;
    }
    if (/\b(tk-\w+|tok)\b/.test(String(child.className))) {
      found.push(child);
    }
    (child.children ?? []).forEach(walk);
  };
  walk(node);
  return found;
}

/** Past the time a hover card stays after the pointer leaves. */
const linger = () => new Promise((resolve) => { setTimeout(resolve, HOVER_LINGER + 20); });

/** Focus from the keyboard, which `:focus-visible` matches; a click's focus does not. */
async function keyboardFocus(node) {
  node.matches = (selector) => selector === ":focus-visible";
  await fire(node, "focus");
  delete node.matches;
}

/** The theorem's key card with `verso.json` served, and its statement's tokens. */
async function versoCard(options = {}) {
  const documentRef = fakeDocument(SHELL);
  const focused = [];
  const create = documentRef.createElement;
  documentRef.createElement = (tag) => Object.assign(create(tag), { focus() { focused.push(this); } });
  const { result } = await start({ documentRef, verso: makeVerso(), ...options });
  await selectPageRegion(documentRef, result.state, "loc:0001");
  const [[, , pre]] = keyCards(documentRef);
  const tokens = tokensOf(pre);
  const named = (text, nth = 0) => tokens.filter((node) => node.textContent === text)[nth];
  return { card: el(documentRef, "hover-card"), documentRef, focused, named, pre, result, tokens };
}

test("verso: a key card's statement carries Verso's tokens and links, and says exactly the snippet", async () => {
  const { pre, tokens } = await versoCard();
  assert.equal(textOf(pre), STATEMENT, "the statement's text is the snippet's, exactly");
  assert.deepEqual(tokens.map((node) => [node.tagName, node.className, node.textContent]), [
    ["SPAN", "tk-kw", "theorem"],
    ["SPAN", "tk-const has-hover", "volume_nonneg"],
    ["SPAN", "tok", "("],
    ["SPAN", "tk-var has-hover", "b"],
    ["A", "tk-const has-hover", "Box"],
    ["SPAN", "tok", ")"],
    ["SPAN", "tk-var has-hover", "b"],
    ["SPAN", "tk-const has-hover", "volume"],
  ]);
  assert.equal(tokens[4].attributes.href, `${ORIGIN}/Notes/Box/index.html#Notes___Box`);
  assert.equal(tokens[7].attributes.href, undefined, "a token never links an address off the site");
  assert.deepEqual(tokens.map((node) => node.tabIndex), [undefined, 0, undefined, -1, -1, undefined, -1, -1],
                   "one tab stop per statement");
  assert.deepEqual(tokens.map((node) => node.dataset.binding), [undefined, undefined, "2", "1", undefined, "2", "1", undefined]);
});

test("verso: a hover opens on the pointer and on focus, lights its binding, and Escape closes it", async () => {
  const { card, documentRef, named, result } = await versoCard();
  assert.equal(card.hidden, true);
  const [b, otherB, box] = [named("b"), named("b", 1), named("Box")];
  await fire(b, "pointerenter", { pointerType: "mouse" });
  assert.equal(card.hidden, false);
  assert.equal(textOf(card), "b : Box", "a variable's hover is its name and type");
  assert.equal(b.attributes["aria-describedby"], "hover-card");
  assert.ok([b, otherB].every((node) => node.classList.contains("is-bound")), "both uses of b are lit");
  await fire(b, "pointerleave", { pointerType: "mouse" });
  assert.ok(![b, otherB].some((node) => node.classList.contains("is-bound")));
  assert.equal(card.hidden, false, "the card waits a moment for the pointer");
  await linger();
  assert.equal(card.hidden, true);
  assert.equal(b.attributes["aria-describedby"], undefined);

  await fire(box, "focus");
  assert.equal(card.hidden, false);
  assert.deepEqual(card.children.map((node) => [node.tagName, node.className]),
                   [["CODE", "hover-sig"], ["P", "hover-doc"], ["P", "hover-doc"]]);
  assert.deepEqual(card.children.map(textOf), ["Notes.Box : Type", "A box: a product of n intervals.", "See Box.volume."]);
  assert.equal(card.children[1].children[1].tagName, "CODE", "inline code in a docstring is code");
  assert.deepEqual([box.tabIndex, named("volume_nonneg").tabIndex], [0, -1], "focus moves the tab stop");
  await fire(documentRef, "keydown", { key: "Escape", target: box });
  assert.equal(card.hidden, true);
  assert.equal(el(documentRef, "selection").hidden, false, "the first Escape only closes the hover");
  assert.equal(result.state.selected?.id, "item:theorem-1.1");
});

test("verso: the pointer can move onto the hover card, which stays while the pointer is on it", async () => {
  const { card, documentRef, named } = await versoCard();
  const [b, box] = [named("b"), named("Box")];
  await fire(b, "pointerenter", { pointerType: "mouse" });
  await fire(b, "pointerleave", { pointerType: "mouse" });
  await fire(card, "pointerenter", { pointerType: "mouse" });
  await linger();
  assert.equal(card.hidden, false, "on the card, the card stays");
  assert.equal(textOf(card), "b : Box");
  await fire(card, "pointerleave", { pointerType: "mouse" });
  await linger();
  assert.equal(card.hidden, true, "off the card, it closes");

  await fire(b, "pointerenter", { pointerType: "mouse" });
  await fire(b, "pointerleave", { pointerType: "mouse" });
  await fire(box, "pointerenter", { pointerType: "mouse" });
  await linger();
  assert.deepEqual([card.hidden, textOf(card.children[0])], [false, "Notes.Box : Type"], "the next token takes the card over");
  await fire(box, "pointerleave", { pointerType: "mouse" });
  await linger();

  // Keyboard focus opens it; a click into the card blurs the token but keeps the card.
  await fire(box, "focus");
  await fire(card, "pointerenter", { pointerType: "mouse" });
  await fire(box, "blur");
  assert.equal(card.hidden, false);
  await fire(documentRef, "click", { target: card });
  assert.equal(card.hidden, false, "a click inside the card keeps it");
  await fire(documentRef, "click", { target: el(documentRef, "page-heading") });
  assert.equal(card.hidden, true, "a click anywhere else closes it");
  await fire(box, "focus");
  await fire(box, "blur");
  assert.equal(card.hidden, true, "focus leaving the token closes it");
});

test("verso: a card keyboard focus opened comes back once the pointer has passed over the statement", async () => {
  const { card, named } = await versoCard();
  const [b, box] = [named("b"), named("Box")];
  await keyboardFocus(box);
  await fire(b, "pointerenter", { pointerType: "mouse" });
  assert.equal(textOf(card), "b : Box", "the pointer's token takes the card");
  await fire(b, "pointerleave", { pointerType: "mouse" });
  await linger();
  assert.deepEqual([card.hidden, textOf(card.children[0]), box.attributes["aria-describedby"]],
                   [false, "Notes.Box : Type", "hover-card"], "then it is the focused token's again");
  let prevented = false;
  Object.assign(card, { clientHeight: 400, scrollHeight: 900, scrollTop: 0 });
  await fire(box, "keydown", { key: "PageDown", preventDefault() { prevented = true; } });
  assert.deepEqual([prevented, card.scrollTop], [true, 376], "and PageDown scrolls it");
  await fire(box, "pointerenter", { pointerType: "mouse" });
  await fire(box, "pointerleave", { pointerType: "mouse" });
  await linger();
  assert.equal(card.hidden, false, "the pointer leaving the focused token leaves its card");
  await fire(box, "blur");
  assert.equal(card.hidden, true);

  // A focused token with a link and no hover has no card to take back.
  const linkOnly = makeVerso();
  linkOnly.statements[VERSO_TARGET][0][7][2] = null;
  const other = await versoCard({ verso: linkOnly });
  await keyboardFocus(other.named("Box"));
  await fire(other.named("b"), "pointerenter", { pointerType: "mouse" });
  await fire(other.named("b"), "pointerleave", { pointerType: "mouse" });
  await linger();
  assert.equal(other.card.hidden, true);
});

test("verso: Escape closes a focused token's card for good, and focus leaving the token forgets it", async () => {
  const { card, documentRef, named } = await versoCard();
  const [b, box] = [named("b"), named("Box")];
  const passOverB = async () => {
    await fire(b, "pointerenter", { pointerType: "mouse" });
    await fire(b, "pointerleave", { pointerType: "mouse" });
    await linger();
  };
  await keyboardFocus(box);
  await fire(documentRef, "keydown", { key: "Escape", target: box });
  assert.equal(card.hidden, true);
  await passOverB();
  assert.equal(card.hidden, true, "the pointer passing over the statement leaves it closed");

  // Tab away from the token while the pointer is on another: the card that goes is the pointer's.
  await keyboardFocus(box);
  await fire(b, "pointerenter", { pointerType: "mouse" });
  await fire(box, "blur");
  await fire(b, "pointerleave", { pointerType: "mouse" });
  await linger();
  assert.deepEqual([card.hidden, box.attributes["aria-describedby"]], [true, undefined]);
});

test("verso: a click's focus leaves the card to the pointer, which closes it shortly after it leaves", async () => {
  const { card, named } = await versoCard();
  const [b, volume] = [named("b"), named("volume")];
  await fire(b, "pointerenter", { pointerType: "mouse" });
  await fire(b, "pointerdown", { pointerType: "mouse" });
  await fire(b, "focus");
  await fire(b, "click");
  await fire(b, "pointerleave", { pointerType: "mouse" });
  await linger();
  assert.equal(card.hidden, true);
  await fire(volume, "pointerenter", { pointerType: "mouse" });
  await fire(volume, "pointerleave", { pointerType: "mouse" });
  await linger();
  assert.equal(card.hidden, true, "nor does the clicked token's card come back");
});

test("verso: PageDown and PageUp scroll a long hover card while its token has focus", async () => {
  const { card, documentRef, named } = await versoCard();
  const box = named("Box");
  await fire(box, "focus");
  const key = async (name) => {
    let prevented = false;
    await fire(box, "keydown", { key: name, preventDefault() { prevented = true; } });
    return [prevented, card.scrollTop];
  };
  Object.assign(card, { clientHeight: 400, scrollHeight: 400, scrollTop: 0 });
  assert.deepEqual(await key("PageDown"), [false, 0], "a card that shows everything leaves the key alone");
  card.scrollHeight = 900;
  assert.deepEqual(await key("PageDown"), [true, 376]);
  assert.deepEqual(await key("PageDown"), [true, 500], "never past the end");
  assert.deepEqual(await key("PageDown"), [false, 500]);
  assert.deepEqual(await key("PageUp"), [true, 124]);
  assert.deepEqual(await key("PageUp"), [true, 0]);
  assert.deepEqual(await key("PageUp"), [false, 0]);
  await fire(named("b"), "focus");
  assert.equal(card.scrollTop, 0, "another token's card starts at its top");

  // A hidden element keeps no scroll position to set, as in a browser: a card is scrolled back once shown.
  let top = 0;
  Object.defineProperty(card, "scrollTop", { configurable: true, get: () => top, set: (value) => { if (!card.hidden) top = value; } });
  await fire(box, "focus");
  assert.deepEqual(await key("PageDown"), [true, 376]);
  await fire(box, "keydown", { key: "Escape", target: box });
  await fire(documentRef, "keydown", { key: "Escape", target: box });
  assert.equal(card.hidden, true);
  await fire(box, "focus");
  assert.deepEqual([card.hidden, card.scrollTop], [false, 0], "the same token's card, reopened, starts at its top");
});

test("verso: the arrow keys move between a statement's tokens and never turn the page", async () => {
  const { focused, named, result } = await versoCard();
  let at = named("Box");
  const moves = [];
  for (const key of ["ArrowRight", "ArrowRight", "ArrowRight", "Home", "ArrowLeft", "End", "ArrowLeft"]) {
    let stopped = false;
    let prevented = false;
    await fire(at, "keydown", { key, preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } });
    at = focused.at(-1);
    moves.push([at.textContent, stopped && prevented]);
  }
  assert.deepEqual(moves.map(([text]) => text), ["b", "volume", "volume", "volume_nonneg", "volume_nonneg", "volume", "b"]);
  assert.ok(moves.every(([, kept]) => kept), "every move is the statement's own");
  let prevented = false;
  await fire(at, "keydown", { key: "Tab", preventDefault() { prevented = true; } });
  assert.equal(prevented, false, "Tab leaves the statement");
  assert.equal(result.state.currentPage, 2, "still the theorem's page");
});

test("verso: a key held with Alt, Ctrl, Command or Shift is the browser's, not the statement's", async () => {
  const { card, focused, named } = await versoCard();
  const box = named("Box");
  await fire(box, "focus");
  Object.assign(card, { clientHeight: 400, scrollHeight: 900, scrollTop: 0 });
  for (const [key, held] of [["ArrowLeft", "altKey"], ["ArrowLeft", "metaKey"], ["Home", "ctrlKey"],
                             ["End", "metaKey"], ["ArrowRight", "shiftKey"], ["PageDown", "altKey"]]) {
    let kept = false;
    const keep = () => { kept = true; };
    await fire(box, "keydown", { key, [held]: true, preventDefault: keep, stopPropagation: keep });
    assert.deepEqual([kept, focused.length, card.scrollTop], [false, 0, 0], `${held} ${key}`);
  }
});

test("verso: on touch a first tap opens the hover and a second follows the link", async () => {
  const { card, named } = await versoCard();
  const box = named("Box");
  await fire(box, "pointerenter", { pointerType: "touch" });
  assert.equal(card.hidden, true, "a touch never hovers");
  const tap = async () => {
    let prevented = false;
    await fire(box, "pointerdown", { pointerType: "touch" });
    await fire(box, "click", { preventDefault() { prevented = true; } });
    return prevented;
  };
  assert.equal(await tap(), true);
  assert.equal(card.hidden, false);
  assert.equal(textOf(card.children[0]), "Notes.Box : Type");
  assert.equal(await tap(), false, "the second tap is the link's");
});

test("verso: on touch a tap inside the card keeps it, and a tap elsewhere closes it", async () => {
  const { card, documentRef, named } = await versoCard();
  const box = named("Box");
  // A tap focuses the token, then clicks it; a tap on the card blurs the token once the finger
  // has lifted, then clicks the card, and the click reaches the document after the card.
  await fire(box, "pointerdown", { pointerType: "touch" });
  await fire(box, "focus");
  await fire(box, "click");
  assert.equal(card.hidden, false);
  await fire(card, "pointerenter", { pointerType: "touch" });
  await fire(card, "pointerdown", { pointerType: "touch" });
  await fire(card, "pointerup", { pointerType: "touch" });
  await fire(box, "blur");
  await fire(card, "click");
  await fire(documentRef, "click", { target: card });
  assert.equal(card.hidden, false, "a tap inside the card keeps it");
  await fire(documentRef, "click", { target: el(documentRef, "page-heading") });
  assert.equal(card.hidden, true, "a tap elsewhere closes it");
  await fire(box, "focus");
  await fire(box, "blur");
  assert.equal(card.hidden, true, "the tap on the card kept that card only");
});

test("verso: a press on the hover card keeps it through that press only", async () => {
  const { card, documentRef, named } = await versoCard();
  const [b, box] = [named("b"), named("Box")];
  // A tap on the card while no token has focus blurs nothing; its click ends it all the same.
  await fire(box, "pointerdown", { pointerType: "touch" });
  await fire(box, "click");
  await fire(card, "pointerdown", { pointerType: "touch" });
  await fire(card, "pointerup", { pointerType: "touch" });
  await fire(card, "click");
  await fire(documentRef, "click", { target: card });
  assert.equal(card.hidden, false);
  await keyboardFocus(b);
  await fire(b, "blur");
  assert.equal(card.hidden, true, "a later blur closes the card");

  // A touch that scrolls the card is canceled, and clicks nothing.
  await keyboardFocus(box);
  await fire(card, "pointerdown", { pointerType: "touch" });
  await fire(card, "pointercancel", { pointerType: "touch" });
  await fire(box, "blur");
  assert.equal(card.hidden, true, "Tab away from the token closes it");

  // A mouse is `over` what it presses, so its press is no tap, even one that clicks nothing.
  await fire(b, "pointerenter", { pointerType: "mouse" });
  await fire(b, "pointerleave", { pointerType: "mouse" });
  await fire(card, "pointerenter", { pointerType: "mouse" });
  await fire(card, "pointerdown", { pointerType: "mouse" });
  await fire(card, "pointerup", { pointerType: "mouse" });
  await fire(card, "pointerleave", { pointerType: "mouse" });
  await fire(b, "pointerenter", { pointerType: "mouse" });
  await keyboardFocus(box);
  await fire(box, "blur");
  assert.equal(card.hidden, true);
});

test("verso: leaving the item closes an open hover", async () => {
  const { card, documentRef, named } = await versoCard();
  await fire(named("volume_nonneg"), "pointerenter", { pointerType: "mouse" });
  assert.equal(card.hidden, false);
  await fire(el(documentRef, "back-to-page"), "click");
  assert.equal(card.hidden, true);
});

test("verso: without verso.json, or with another statement in it, a card keeps its plain statement", async () => {
  const other = makeVerso();
  other.statements[VERSO_TARGET][0][0] = ["kw", "lemma"];
  for (const [label, verso] of [["absent", undefined], ["not JSON", "{oops"], ["another text", other],
                                ["another schema", { ...makeVerso(), schema: "x" }]]) {
    const { documentRef, result } = await start({ verso });
    assert.equal(el(documentRef, "status").textContent, "Verified PDF", label);
    assert.equal(el(documentRef, "error").hidden, true, label);
    await selectPageRegion(documentRef, result.state, "loc:0001");
    const [[, , pre]] = keyCards(documentRef);
    assert.equal(textOf(pre), STATEMENT, label);
    assert.deepEqual(tokensOf(pre).map((node) => node.className), ["tk-kw"], `${label}: the plain tint`);
  }
});

test("verso: the hover card sits under its token, above it near the window's foot, and inside the window", async () => {
  const documentRef = fakeDocument(SHELL);
  const create = documentRef.createElement;
  documentRef.createElement = (tag) => Object.assign(create(tag), {
    getBoundingClientRect() { return this.rect ?? { bottom: 0, left: 0, right: 0, top: 0 }; },
  });
  const windowRef = { innerHeight: 800, innerWidth: 1000 };
  const { result } = await start({ documentRef, verso: makeVerso(), windowRef });
  await selectPageRegion(documentRef, result.state, "loc:0001");
  const card = el(documentRef, "hover-card");
  card.getBoundingClientRect = () => ({ height: 100, width: 300 });
  const [[, , pre]] = keyCards(documentRef);
  const box = tokensOf(pre).find((node) => node.textContent === "Box");
  const place = async (rect) => {
    box.rect = rect;
    await fire(box, "pointerenter", { pointerType: "mouse" });
    const at = [card.style.left, card.style.top, card.hidden];
    await fire(box, "pointerleave", { pointerType: "mouse" });
    return at;
  };
  assert.deepEqual(await place({ bottom: 120, left: 200, right: 230, top: 100 }), ["200px", "126px", false]);
  assert.deepEqual(await place({ bottom: 760, left: 200, right: 230, top: 740 }), ["200px", "634px", false],
                   "no room below: above the token");
  assert.deepEqual(await place({ bottom: 120, left: 900, right: 930, top: 100 }), ["692px", "126px", false],
                   "never past the window's right edge");
  assert.deepEqual(await place({ bottom: -10, left: 200, right: 230, top: -30 }).then((at) => at[2]), true,
                   "a token scrolled away hides its card");

  // A card is as wide as its content allows from where it stands (shrink to fit): measured where the
  // last one was left, a wide card near the right edge would come out narrow.
  let natural = 50;
  card.getBoundingClientRect = () => ({ height: 100, width: Math.min(natural, 1000 - (parseFloat(card.style.left) || 0)) });
  assert.deepEqual(await place({ bottom: 120, left: 940, right: 970, top: 100 }), ["940px", "126px", false]);
  natural = 300;
  const other = tokensOf(pre).find((node) => node.textContent === "volume_nonneg");
  other.rect = { bottom: 120, left: 900, right: 930, top: 100 };
  await fire(other, "pointerenter", { pointerType: "mouse" });
  assert.deepEqual([card.style.left, card.getBoundingClientRect().width], ["692px", 300],
                   "the next card is measured at its own width");
});

test("verso: a token scrolled out of the panel hides its card, and Escape then leaves the item", async () => {
  const documentRef = fakeDocument(SHELL);
  const create = documentRef.createElement;
  documentRef.createElement = (tag) => Object.assign(create(tag), {
    getBoundingClientRect() { return this.rect ?? { bottom: 0, left: 0, right: 0, top: 0 }; },
  });
  const windowRef = { innerHeight: 800, innerWidth: 1000 };
  const { result } = await start({ documentRef, verso: makeVerso(), windowRef });
  await selectPageRegion(documentRef, result.state, "loc:0001");
  const [card, details] = [el(documentRef, "hover-card"), el(documentRef, "details")];
  card.getBoundingClientRect = () => ({ height: 100, width: 300 });
  details.getBoundingClientRect = () => ({ bottom: 800, left: 600, right: 1000, top: 200 });
  details.contains = () => true;
  const [[, , pre]] = keyCards(documentRef);
  const box = tokensOf(pre).find((node) => node.textContent === "Box");
  const scrollTo = async (top) => {
    box.rect = { bottom: top + 20, left: 700, right: 730, top };
    await fire(documentRef, "scroll");
    return card.hidden;
  };
  box.rect = { bottom: 320, left: 700, right: 730, top: 300 };
  await fire(box, "focus");
  assert.equal(card.hidden, false);
  assert.equal(await scrollTo(150), true, "above the panel's top, though still in the window");
  assert.equal(await scrollTo(300), false, "back in the panel, the card is back");
  assert.equal(await scrollTo(150), true);
  await fire(documentRef, "keydown", { key: "Escape", target: box });
  assert.equal(result.state.selected, null, "an Escape with no card in sight leaves the item");
  assert.equal(box.attributes["aria-describedby"], undefined);
});

test("verso: focus on a token the clamp hides opens the whole statement", async () => {
  const sections = makeSections(PDF_SHA256);
  const lines = Array.from({ length: 24 }, (_, n) => `  h${n} : b = b`);
  const text = `theorem volume_nonneg (b : Box) :\n${lines.join("\n")}`;
  linkRecord(sections, "item:theorem-1.1", "key", "Notes.Box.volume_nonneg").object[0].extensions[NAMESPACE].snippet.text = text;
  const verso = makeVerso();
  const tail = lines.flatMap((line, n) => [["tx", `\n  h${n} : `], ["var", "b", 2, 1], ["tx", " = "], ["var", "b", 2, 1]]);
  verso.statements[VERSO_TARGET][0] = [...verso.statements[VERSO_TARGET][0].slice(0, 9), ["tx", " :"], ...tail];
  const documentRef = fakeDocument(SHELL);
  const create = documentRef.createElement;
  documentRef.createElement = (tag) => Object.assign(create(tag), {
    getBoundingClientRect() { return this.rect ?? { bottom: 0, left: 0, right: 0, top: 0 }; },
  });
  const { result } = await start({ documentRef, sections, verso });
  await selectPageRegion(documentRef, result.state, "loc:0001");
  const [[, , pre, button]] = keyCards(documentRef);
  assert.equal(textOf(pre), text);
  assert.deepEqual([pre.className, button.textContent], ["lean is-clamped", "Show all"]);
  pre.rect = { bottom: 300, left: 0, right: 600, top: 0 };
  const tokens = tokensOf(pre).filter((node) => node.textContent === "b");
  tokens[1].rect = { bottom: 120, left: 0, right: 10, top: 100 };
  await fire(tokens[1], "focus");
  assert.equal(pre.className, "lean is-clamped", "a token in sight leaves the clamp");
  tokens.at(-1).rect = { bottom: 520, left: 0, right: 10, top: 500 };
  await fire(tokens.at(-1), "focus");
  assert.deepEqual([pre.className, button.textContent, button.attributes["aria-expanded"]], ["lean", "Show less", "true"]);

  // A browser scrolls the clamped block to the focused token, which is then in sight inside it.
  await fire(button, "click");
  assert.equal(pre.className, "lean is-clamped");
  let shown = null;
  tokens.at(-2).scrollIntoView = (options) => { shown = options; };
  pre.scrollTop = 380;
  await fire(tokens.at(-2), "focus");
  assert.deepEqual([pre.className, pre.scrollTop, shown], ["lean", 0, { block: "nearest" }],
                   "a block focus scrolled opens, back at its top, with the token in view");
});

// --- proof steps --------------------------------------------------------------

const PROOF_FULL = { sections: makeProofSections(PDF_SHA256) };
const PROOF_MINIMAL = { ...PROOF_FULL, config: makeConfig(MINIMAL) };

/** The step rows nested under each index row, as `[its item's title, [step text, ...]]`. */
function nestedRows(documentRef) {
  return el(documentRef, "page-index").children.map((li) => [
    textOf(li.children[0].children[1]),
    (li.children[1]?.children ?? []).map((step) => step.children[0].children.slice(1).map(textOf).join("|")),
  ]);
}

async function openStep(documentRef, itemId) {
  const row = el(documentRef, "page-index").children.flatMap((li) => li.children[1]?.children ?? [])
    .map((li) => li.children[0]).find((button) => button.dataset.itemId === itemId);
  assert.ok(row, `${itemId} has no row on this page`);
  await fire(row, "click");
}

test("steps: the index nests each step under its item, with its number, text and relation", async () => {
  const { documentRef } = await start(PROOF_FULL);
  await turnTo(documentRef, 9);
  assert.equal(el(documentRef, "index-meta").textContent, "2 passages · 2 with a key declaration · 2 proof steps");
  assert.deepEqual(nestedRows(documentRef), [
    ["Theorem 9.1", ["1|Unfold the product|Exact match", "2|Collect the two terms|Close match"]],
    ["Remark 9.2", []],
  ]);
  assert.equal(el(documentRef, "page-index").children.length, 2, "two rows, not four");
});

test("steps: a page with only an item's later steps brings the item with it, and its row opens that page", async () => {
  const { documentRef, result } = await start(PROOF_FULL);
  await turnTo(documentRef, 10);
  assert.equal(el(documentRef, "index-meta").textContent, "2 proof steps");
  assert.deepEqual(nestedRows(documentRef), [
    ["Theorem 9.1", ["3|Bound each side|Related", "4|The sum is finite|No match"]],
  ]);
  const [row] = indexRows(documentRef);
  assert.equal(row.dataset.regionId, undefined);
  assert.match(textOf(row.children[2]), /p\. 9$/);
  await fire(row, "click");
  assert.equal(result.state.currentPage, 9);
  assert.equal(itemTitle(documentRef), "Theorem 9.1");
  assert.equal(result.state.region.page, 9);
});

test("steps: a box and a row light each other, and a step's box is marked as one", async () => {
  const { documentRef, result } = await start(PROOF_FULL);
  await turnTo(documentRef, 9);
  const step = result.state.index.items.get("a");
  const polygon = polygonOf(documentRef, step.place.regionId);
  assert.equal(polygon.dataset.step, "true");
  assert.equal(polygonOf(documentRef, result.state.index.items.get("thm").place.regionId).dataset.step, undefined);
  const row = el(documentRef, "page-index").children[0].children[1].children[0].children[0];
  await fire(polygon, "pointerenter", { pointerType: "mouse" });
  assert.ok(row.classList.contains("is-hot"));
  await fire(polygon, "pointerleave");
  assert.ok(!row.classList.contains("is-hot"));
});

test("steps: a step's card shows its Lean lines, one range each, not the declaration again and again", async () => {
  const { documentRef } = await start(PROOF_FULL);
  await turnTo(documentRef, 9);
  await openStep(documentRef, "b");
  assert.equal(itemTitle(documentRef), "Step 2");
  assert.equal(textOf(el(documentRef, "item-label").children[1]), "Collect the two terms");
  assert.equal(el(documentRef, "key-list").children.length, 1, "three ranges of one declaration are one card");
  const [[, name, ...ranges]] = keyCards(documentRef);
  assert.equal(textOf(name), "Box.double");
  const pres = ranges.filter((node) => node.tagName === "PRE");
  assert.deepEqual(pres.map((pre) => pre.attributes["aria-label"]), ["Lines 22–23", "Line 25", "Lines 27–40"]);
  assert.deepEqual(pres.map(textOf), ["rw [two_mul]\nring", "exact h", "calc _ = _ := by\n  simp"],
                   "the text is the snippet's, exactly");
  assert.deepEqual(pres.map((pre) => pre.children.map((line) => line.dataset.line)),
                   [["22", "23"], ["25"], ["27", "28"]], "and each line carries its number");
  assert.ok(pres.every((pre) => pre.classList.contains("is-numbered")));
  assert.deepEqual(ranges.filter((node) => node.tagName !== "PRE").map(textOf), ["… through line 40"],
                   "a cut range says how far it runs");
  assert.ok(!pres.map(textOf).includes("theorem double (b : Box) : 2 * b = b + b"), "the whole statement is not repeated");
});

test("steps: every numbered range uses its own Verso snippet and opens semantic hovers", async () => {
  const verso = makeVerso();
  verso.statements["Notes/Box/index.html#Notes___Box___double"] = [
    [["kw", "rw"], ["tx", " ["], ["const", "two_mul", 101], ["tx", "]\n"], ["kw", "ring"]],
    [["kw", "exact"], ["tx", " "], ["var", "h", 102]],
  ];
  verso.hovers[101] = { signature: "two_mul (n : Nat) : 2 * n = n + n" };
  verso.hovers[102] = { signature: "b = b" };
  const { documentRef } = await start({ ...PROOF_FULL, verso });
  await turnTo(documentRef, 9);
  await openStep(documentRef, "b");
  const pres = keyCards(documentRef)[0].filter((node) => node.tagName === "PRE");
  assert.deepEqual(pres.map(textOf), ["rw [two_mul]\nring", "exact h", "calc _ = _ := by\n  simp"]);
  const theorem = tokensOf(pres[0]).find((node) => node.textContent === "two_mul");
  assert.ok(theorem.classList.contains("has-hover"));
  await fire(theorem, "pointerenter", { pointerType: "mouse" });
  assert.equal(textOf(el(documentRef, "hover-card")), "two_mul (n : Nat) : 2 * n = n + n");
  const local = tokensOf(pres[1]).find((node) => node.textContent === "h");
  await keyboardFocus(local);
  assert.equal(textOf(el(documentRef, "hover-card")), "h : b = b");
  assert.deepEqual(pres.map((pre) => pre.children[0].dataset.line), ["22", "25", "27"]);
});

test("steps: a step with lines of two declarations shows a card for each", async () => {
  const { documentRef } = await start(PROOF_FULL);
  await turnTo(documentRef, 10);
  await openStep(documentRef, "c");
  assert.deepEqual(keyCards(documentRef).map(([, name]) => textOf(name)), ["Box.double", "Box.bound"]);
});

test("steps: the note's backticked code is set as code, on a step only", async () => {
  const { documentRef } = await start(PROOF_FULL);
  await turnTo(documentRef, 9);
  await openStep(documentRef, "b");
  const [code, rest] = el(documentRef, "coverage-note").children;
  assert.deepEqual([code.tagName, code.textContent, rest], ["CODE", "ring", " closes what the book does by hand."]);
  const definition = await start();
  await selectPageRegion(definition.documentRef, definition.result.state, "loc:0002");
  assert.deepEqual(el(definition.documentRef, "coverage-note").children, []);
});

test("steps: the relation is printed, in place of the coverage word it restates", async () => {
  const { documentRef } = await start(PROOF_FULL);
  await turnTo(documentRef, 9);
  await openStep(documentRef, "a");
  assert.equal(el(documentRef, "target-summary").textContent, "Exact match");
  assert.equal(el(documentRef, "target-summary").hidden, false);
  assert.equal(el(documentRef, "target-summary").title, "The Lean lines make this step's move.");
  await fire(el(documentRef, "back-to-page"), "click");
  await openStep(documentRef, "b");
  assert.equal(el(documentRef, "target-summary").textContent, "Close match");
  assert.match(el(documentRef, "target-summary").className, /coverage-formalized/, "the mark is still the region's");
});

test("minimal: an exact match goes unmarked; a relation that qualifies the link stays", async () => {
  const { documentRef } = await start(PROOF_MINIMAL);
  await turnTo(documentRef, 9);
  assert.deepEqual(nestedRows(documentRef)[0][1], ["1|Unfold the product", "2|Collect the two terms|Close match"]);
  await openStep(documentRef, "a");
  assert.equal(el(documentRef, "target-summary").hidden, true);
  await fire(el(documentRef, "back-to-page"), "click");
  await openStep(documentRef, "b");
  assert.equal(el(documentRef, "target-summary").hidden, false);
  assert.equal(el(documentRef, "target-summary").textContent, "Close match");
  await turnTo(documentRef, 10);
  assert.deepEqual(nestedRows(documentRef)[0][1], ["3|Bound each side|Related", "4|The sum is finite|No match"]);
});

test("minimal: a step's card keeps its link, its name and its lines, and drops its kind and badges", async () => {
  const { documentRef } = await start(PROOF_MINIMAL);
  await turnTo(documentRef, 10);
  await openStep(documentRef, "c");
  const [, [eyebrow, name, pre]] = keyCards(documentRef);
  assert.equal(textOf(eyebrow.children[0]), "", "no kind and no sorry badge");
  assert.equal(eyebrow.children[1].tagName, "A");
  assert.equal(textOf(name), "Box.bound");
  assert.equal(pre.attributes["aria-label"], "Line 7");
  assert.equal(el(documentRef, "provenance-note").hidden, true);
  const full = await start(PROOF_FULL);
  await turnTo(full.documentRef, 10);
  await openStep(full.documentRef, "c");
  assert.match(textOf(keyCards(full.documentRef)[1][0].children[0]), /sorry: exercise/);
  assert.equal(el(full.documentRef, "provenance-note").textContent, "Audit links, unreviewed.");
});

for (const [mode, options] of [["full", PROOF_FULL], ["minimal", PROOF_MINIMAL]]) {
  test(`steps: a checked absence reads as one, with where it was checked (${mode})`, async () => {
    const { documentRef } = await start(options);
    await turnTo(documentRef, 10);
    await openStep(documentRef, "d");
    assert.equal(el(documentRef, "target-summary").textContent, "No match");
    assert.equal(textOf(el(documentRef, "coverage-note")), "Lean takes finiteness from an instance.");
    const [lead, heading, list] = el(documentRef, "key-note").children;
    assert.equal(el(documentRef, "key-note").hidden, false);
    assert.equal(textOf(lead), "Checked: no Lean line makes this step.");
    assert.equal(textOf(heading), "Checked in");
    assert.deepEqual(list.children.map(textOf), ["Box.double", "Notes.Setlines 50–52"]);
    const [named, bare] = list.children.map((li) => li.children[0]);
    assert.deepEqual([named.tagName, named.attributes.href],
                     ["A", `${ORIGIN}/Notes/Box/index.html#Notes___Box___double`], "linked through the theorem's card");
    assert.deepEqual([bare.tagName, bare.attributes.href], ["SPAN", undefined]);
    assert.ok(!/No Lean declaration is linked/.test(textOf(el(documentRef, "key-note"))));
    assert.deepEqual(el(documentRef, "key-list").children, []);
  });
}

test("steps: a checked absence that is not a step still says nothing is linked", async () => {
  const { documentRef } = await start(PROOF_FULL);
  await turnTo(documentRef, 11);
  await fire(indexRows(documentRef)[0], "click");
  assert.equal(textOf(el(documentRef, "key-note")), "No Lean declaration is linked to this passage.");
  assert.equal(el(documentRef, "target-summary").textContent, "Not formalized");
});

test("steps: an item lists its proof's steps, across pages, and a row opens its step on its page", async () => {
  const { documentRef, result } = await start(PROOF_FULL);
  await turnTo(documentRef, 9);
  await fire(indexRows(documentRef)[0], "click");
  assert.equal(itemTitle(documentRef), "Theorem 9.1");
  assert.equal(el(documentRef, "step-section").hidden, false);
  const steps = rows(documentRef, "step-list").map((li) => li.children[0]);
  assert.deepEqual(steps.map((step) => step.children.slice(1).map(textOf).join("|")), [
    "1|Unfold the product|Exact match", "2|Collect the two terms|Close match",
    "3|Bound each side|Related|p. 10", "4|The sum is finite|No match|p. 10",
  ]);
  await fire(steps[2], "click");
  assert.equal(result.state.currentPage, 10);
  assert.equal(itemTitle(documentRef), "Step 3");
  assert.equal(el(documentRef, "selection").hidden, false, "no page index between the two");
  assert.equal(el(documentRef, "page-number").value, "10");
});

test("steps: the stepper walks the proof, turning the page, and the head leads back to the item", async () => {
  const { documentRef, result } = await start(PROOF_FULL);
  await turnTo(documentRef, 9);
  await openStep(documentRef, "b");
  assert.equal(el(documentRef, "item-position").textContent, "2 of 4");
  assert.equal(el(documentRef, "next-item").attributes["aria-label"], "Next step");
  await fire(el(documentRef, "next-item"), "click");
  assert.equal(result.state.selected.id, "c");
  assert.equal(result.state.currentPage, 10);
  assert.equal(el(documentRef, "item-position").textContent, "3 of 4");
  await fire(el(documentRef, "next-item"), "click");
  assert.equal(el(documentRef, "next-item").disabled, true);
  assert.equal(el(documentRef, "step-parent").textContent, "Proof of Theorem 9.1");
  assert.equal(el(documentRef, "step-parent").hidden, false);
  await fire(el(documentRef, "step-parent"), "click");
  assert.equal(result.state.selected.id, "thm");
  assert.equal(result.state.currentPage, 9);
  assert.equal(el(documentRef, "step-parent").hidden, true);
  // On an item the stepper is back to the page's items, which are not its steps.
  assert.equal(el(documentRef, "item-position").textContent, "1 of 2");
  assert.equal(el(documentRef, "next-item").attributes["aria-label"], "Next passage");
  await fire(el(documentRef, "next-item"), "click");
  assert.equal(result.state.selected.id, "remark");
});

test("steps: a click on a step's box opens the step, and the way back lands on its row", async () => {
  const documentRef = fakeDocument(SHELL);
  const created = documentRef.createElement;
  let focused = null;
  documentRef.createElement = (tag) => {
    const node = created(tag);
    node.focus = () => {
      focused = node;
    };
    return node;
  };
  const { result } = await start({ ...PROOF_FULL, documentRef });
  await turnTo(documentRef, 9);
  await fire(polygonOf(documentRef, result.state.index.items.get("b").place.regionId), "click");
  assert.equal(result.state.selected.id, "b");
  assert.equal(itemTitle(documentRef), "Step 2");
  await fire(el(documentRef, "back-to-page"), "click");
  assert.equal(result.state.selected, null);
  assert.equal(focused.dataset.itemId, "b");
});

test("steps: a step tied to no item keeps its own row, with its lines and its relation", async () => {
  const proof = makeProofSections(PDF_SHA256);
  const [theorem, , second, , fourth] = proof["9/theorem-9.1"];
  const { documentRef } = await start({ sections: { 9: [theorem, second], 10: [fourth] } });
  await turnTo(documentRef, 9);
  assert.equal(el(documentRef, "page-index").children.length, 2);
  const [, row] = indexRows(documentRef);
  assert.equal(textOf(row.children[2]), "Box.double Close match", "three ranges of one declaration, no \"+2\"");
  await fire(row, "click");
  assert.equal(itemTitle(documentRef), "b: Collect the two terms");
  assert.equal(el(documentRef, "step-parent").hidden, true);
  assert.equal(el(documentRef, "target-summary").textContent, "Close match");
  assert.equal(el(documentRef, "key-list").children.length, 1);
  assert.equal(el(documentRef, "item-position").textContent, "2 of 2", "the stepper walks the page");
  await turnTo(documentRef, 10);
  assert.equal(textOf(indexRows(documentRef)[0].children[2]), "No match");
});

test("steps: a companion with no steps shows no step section, no step mark and the same index", async () => {
  const { documentRef, result } = await start();
  await turnTo(documentRef, 2);
  assert.deepEqual(el(documentRef, "page-index").children.map((li) => li.children.length), [1, 1], "no nested list");
  assert.equal(el(documentRef, "overlay").children.some((polygon) => polygon.dataset.step), false);
  await selectPageRegion(documentRef, result.state, "loc:0001");
  assert.equal(el(documentRef, "step-section").hidden, true);
  assert.equal(el(documentRef, "step-parent").hidden, true);
  assert.equal(el(documentRef, "next-item").attributes["aria-label"], "Next passage");
  assert.equal(el(documentRef, "target-summary").textContent, "Formalized", "a relation is not printed on an item");
  const [[, , statement]] = keyCards(documentRef);
  assert.equal(statement.classList.contains("is-numbered"), false);
  assert.equal(statement.children[0].dataset.line, undefined);
});

// --- a record's note, and scripts in titles -----------------------------------

function noteState(documentRef) {
  const [toggle, note] = [el(documentRef, "note-toggle"), el(documentRef, "coverage-note")];
  return { expanded: toggle.attributes["aria-expanded"], note: !note.hidden, toggle: toggle.hidden ? "" : toggle.textContent };
}

test("minimal: a step's note opens on asking, and its claim never waits for that", async () => {
  const { documentRef } = await start(PROOF_MINIMAL);
  await turnTo(documentRef, 9);
  await openStep(documentRef, "b");
  assert.deepEqual(noteState(documentRef), { expanded: "false", note: false, toggle: "More detail" });
  assert.equal(el(documentRef, "target-summary").textContent, "Close match");
  await fire(el(documentRef, "note-toggle"), "click");
  assert.deepEqual(noteState(documentRef), { expanded: "true", note: true, toggle: "Less detail" });
  assert.equal(textOf(el(documentRef, "coverage-note")), "ring closes what the book does by hand.");
  await fire(el(documentRef, "note-toggle"), "click");
  assert.deepEqual(noteState(documentRef), { expanded: "false", note: false, toggle: "More detail" });
});

for (const [mode, config] of [["full", makeConfig()], ["minimal", makeConfig(MINIMAL)]]) {
  test(`notes: a repeated step heading has no detail text or toggle (${mode})`, async () => {
    const sections = makeProofSections(PDF_SHA256);
    const step = sections["9/theorem-9.1"].find(record => record.id === "b");
    step.note = "  Collect the two\n terms  ";
    const { documentRef } = await start({ config, sections });
    await turnTo(documentRef, 9);
    await openStep(documentRef, "b");
    assert.match(textOf(el(documentRef, "item-label")), /Collect the two terms/);
    assert.equal(el(documentRef, "coverage-note").hidden, true);
    assert.equal(el(documentRef, "note-toggle").hidden, true);
    assert.equal(el(documentRef, "target-summary").textContent, "Close match");
    await fire(el(documentRef, "next-item"), "click");
    await fire(el(documentRef, "next-item"), "click");
    assert.equal(textOf(el(documentRef, "coverage-note")), "Lean takes finiteness from an instance.");
    assert.equal(el(documentRef, "note-toggle").hidden, mode !== "minimal");
    assert.equal(el(documentRef, "coverage-note").hidden, mode === "minimal");
  });
}

test("minimal: a checked absence is the claim, and stays in sight with its note closed", async () => {
  const { documentRef } = await start(PROOF_MINIMAL);
  await turnTo(documentRef, 10);
  await openStep(documentRef, "d");
  assert.deepEqual(noteState(documentRef), { expanded: "false", note: false, toggle: "More detail" });
  assert.equal(el(documentRef, "target-summary").textContent, "No match");
  assert.equal(el(documentRef, "key-note").hidden, false);
  assert.equal(textOf(el(documentRef, "key-note").children[0]), "Checked: no Lean line makes this step.");
});

test("minimal: an opened note stays open from record to record; a record with no note has no control", async () => {
  const { documentRef } = await start(PROOF_MINIMAL);
  await turnTo(documentRef, 9);
  await openStep(documentRef, "b");
  await fire(el(documentRef, "note-toggle"), "click");
  await fire(el(documentRef, "next-item"), "click");
  assert.equal(el(documentRef, "note-toggle").hidden, true, "step c has no note");
  assert.equal(el(documentRef, "coverage-note").hidden, true);
  await fire(el(documentRef, "next-item"), "click");
  assert.deepEqual(noteState(documentRef), { expanded: "true", note: true, toggle: "Less detail" });
  assert.equal(textOf(el(documentRef, "coverage-note")), "Lean takes finiteness from an instance.");
});

test("minimal: another record's note waits too, and a plain record is as it was", async () => {
  const { documentRef, result } = await start({ config: makeConfig(MINIMAL) });
  await selectPageRegion(documentRef, result.state, "loc:0002");
  assert.deepEqual(noteState(documentRef), { expanded: "false", note: false, toggle: "More detail" });
  await fire(el(documentRef, "note-toggle"), "click");
  assert.equal(el(documentRef, "coverage-note").textContent,
               "The companion states the neighbouring box lemma, not this definition.");
  assert.equal(el(documentRef, "coverage-note").hidden, false);
  await selectPageRegion(documentRef, result.state, "loc:0001");
  assert.equal(el(documentRef, "note-toggle").hidden, true);
  assert.equal(el(documentRef, "coverage-note").hidden, true);
});

for (const [what, options] of [["a step", PROOF_FULL], ["no step", undefined]]) {
  test(`full: a note shows with no control (${what})`, async () => {
    const { documentRef, result } = await start(options);
    if (options) {
      await turnTo(documentRef, 9);
      await openStep(documentRef, "b");
    } else {
      await selectPageRegion(documentRef, result.state, "loc:0002");
    }
    assert.equal(el(documentRef, "note-toggle").hidden, true);
    assert.equal(el(documentRef, "coverage-note").hidden, false);
  });
}

/** The proof fixture, with its theorem and step "a" titled with script characters. */
function scriptedSections() {
  const sections = makeProofSections(PDF_SHA256);
  const proof = sections["9/theorem-9.1"];
  proof[0].subject[0].selector = { ...proof[0].subject[0].selector };
  const retitle = (record, from, to) => {
    const before = JSON.stringify(record);
    assert.ok(before.includes(from), `no "${from}" in the fixture`);
    return JSON.parse(before.replace(from, to));
  };
  proof[0] = retitle(proof[0], "Theorem 9.1 (Doubling)", "Theorem 9.1 (eₖ ≠ 0 for xⁿ)");
  proof[1] = retitle(proof[1], "a: Unfold the product", "a: fₖ ≠ 0, ‖eₘ‖ = 1 and x⁻¹");
  return sections;
}

/** A node's text with its `<sub>` and `<sup>` children marked, as `f_{k}` and `x^{n}`. */
function scripted(node) {
  if (typeof node === "string") {
    return node;
  }
  const inner = (node.textContent ?? "") + node.children.map(scripted).join("");
  return node.tagName === "SUB" ? `_{${inner}}` : node.tagName === "SUP" ? `^{${inner}}` : inner;
}

for (const [mode, config] of [["full", makeConfig()], ["minimal", makeConfig(MINIMAL)]]) {
  test(`titles: script characters are real <sub> and <sup> on every surface (${mode})`, async () => {
    const { documentRef } = await start({ config, sections: scriptedSections() });
    await turnTo(documentRef, 9);
    const row = el(documentRef, "page-index").children.find((li) => li.children[1]);
    assert.equal(scripted(row.children[0].children[1].children[0]), "Theorem 9.1");
    const step = row.children[1].children[0].children[0];
    assert.equal(scripted(step.children[2]), "f_{k} ≠ 0, ‖e_{m}‖ = 1 and x^{−1}");
    assert.equal(step.title, "a: fₖ ≠ 0, ‖eₘ‖ = 1 and x⁻¹", "a tooltip keeps the label as written");
    await openStep(documentRef, "a");
    const [title, subtitle] = el(documentRef, "item-label").children;
    assert.equal(textOf(title), "Step 1");
    assert.equal(scripted(subtitle), "f_{k} ≠ 0, ‖e_{m}‖ = 1 and x^{−1}");
    await fire(el(documentRef, "step-parent"), "click");
    const [item, itemSubtitle] = el(documentRef, "item-label").children;
    assert.equal(scripted(item), "Theorem 9.1");
    assert.equal(scripted(itemSubtitle), "e_{k} ≠ 0 for x^{n}");
    assert.equal(el(documentRef, "step-parent").hidden, true);
  });
}

for (const [mode, config] of [["full", makeConfig()], ["minimal", makeConfig(MINIMAL)]]) {
  test(`titles: capital and nested subscripts render in overview and details (${mode})`, async () => {
    const label = "ι_{S} : S → X, Π₁(ι_{Uᵢ}), γ_{[r,s]} and x^{Q}";
    const sections = makeProofSections(PDF_SHA256);
    sections["9/theorem-9.1"][0] = JSON.parse(JSON.stringify(sections["9/theorem-9.1"][0])
      .replace("Theorem 9.1 (Doubling)", label));
    const { documentRef } = await start({ config, sections });
    await turnTo(documentRef, 9);
    const row = el(documentRef, "page-index").children.find((li) => li.children[1]);
    const expected = "ι_{S} : S → X, Π_{1}(ι_{U_{i}}), γ_{[r,s]} and x^{Q}";
    assert.equal(scripted(row.children[0].children[1].children[0]), expected);
    await openStep(documentRef, "a");
    await fire(el(documentRef, "step-parent"), "click");
    assert.equal(scripted(el(documentRef, "item-label").children[0]), expected);
  });
}

test("titles: a step's parent button sets its item's scripts", async () => {
  const sections = scriptedSections();
  sections["9/theorem-9.1"][0] = JSON.parse(JSON.stringify(sections["9/theorem-9.1"][0])
    .replace("Theorem 9.1 (eₖ ≠ 0 for xⁿ)", "Theorem eₖ"));
  const { documentRef } = await start({ sections });
  await turnTo(documentRef, 9);
  await openStep(documentRef, "a");
  assert.equal(scripted(el(documentRef, "step-parent")), "Proof of Theorem e_{k}");
  await fire(el(documentRef, "next-item"), "click");
  assert.equal(scripted(el(documentRef, "step-parent")), "Proof of Theorem e_{k}", "and redraws it once");
});

test("titles: a label with no script characters stays one text, as it was", async () => {
  const { documentRef } = await start(PROOF_FULL);
  await turnTo(documentRef, 9);
  await openStep(documentRef, "a");
  const [, subtitle] = el(documentRef, "item-label").children;
  assert.deepEqual([subtitle.textContent, subtitle.children], ["Unfold the product", []]);
  assert.deepEqual(el(documentRef, "step-parent").children, []);
});

test("fonts: the serif and code stacks end in the system font, after their generic family", async () => {
  const css = await (await import("node:fs/promises")).readFile(
    (await import("node:path")).join((await import("./fixtures.mjs")).VIEWER_DIR, "viewer.css"), "utf8");
  assert.match(css, /--serif:[^;]*, serif, system-ui;/);
  assert.match(css, /--mono:[^;]*, monospace, system-ui;/);
});


// --- whole-document find ----------------------------------------------------

async function findPdf(documentRef, query) {
  await fire(el(documentRef, "find-toggle"), "click");
  el(documentRef, "find-input").value = query;
  await fire(el(documentRef, "find-input"), "input");
}

test("find: Cmd+F and Ctrl+F open the PDF search, even from another input", async () => {
  const { documentRef } = await start();
  for (const modifier of ["metaKey", "ctrlKey"]) {
    let prevented = false;
    await fire(documentRef, "keydown", { key: "f", [modifier]: true, target: { tagName: "INPUT" },
      preventDefault: () => { prevented = true; } });
    assert.equal(prevented, true);
    assert.equal(el(documentRef, "find-bar").hidden, false);
    await fire(documentRef, "keydown", { key: "Escape", target: el(documentRef, "find-input") });
    assert.equal(el(documentRef, "find-bar").hidden, true);
  }
});

test("find: all-page results navigate and wrap with Enter and Shift+Enter, retaining reading mode", async () => {
  const pdfjs = fakePdfjs({ numPages: 3, pageTexts: ["path path", "nothing", "a Path"] });
  const { documentRef, result } = await start({ pdfjs });
  await fire(el(documentRef, "toggle-reading"), "click");
  await findPdf(documentRef, "PATH");
  assert.equal(el(documentRef, "find-count").textContent, "1 of 3");
  assert.equal(pdfjs.textReads.length, 3, "page three is searched while page one is displayed");
  await fire(documentRef, "keydown", { key: "Enter", target: el(documentRef, "find-input") });
  assert.equal(el(documentRef, "find-count").textContent, "2 of 3");
  await fire(el(documentRef, "find-next"), "click");
  assert.equal(result.state.currentPage, 3);
  assert.equal(el(documentRef, "find-count").textContent, "3 of 3");
  await fire(el(documentRef, "find-next"), "click");
  assert.equal(result.state.currentPage, 1);
  await fire(documentRef, "keydown", { key: "Enter", shiftKey: true, target: el(documentRef, "find-input") });
  assert.equal(result.state.currentPage, 3);
  assert.equal(result.state.readingMode, true);
  await fire(documentRef, "keydown", { key: "Escape", target: el(documentRef, "find-input") });
  assert.equal(result.state.readingMode, true, "the first Escape only closes search");
  assert.equal(el(documentRef, "find-bar").hidden, true);
  assert.deepEqual(el(documentRef, "find-highlights").children, []);
});

test("find: missing text, no results and extraction failures are explicit and do not disable reading", async () => {
  for (const [texts, query, expected] of [
    [["path", "nothing"], "other", /No matches/],
    [["", ""], "path", /No searchable text/],
    [[new Error("broken"), "path"], "path", /1 page could not be searched/],
  ]) {
    const { documentRef, result } = await start({ pdfjs: fakePdfjs({ numPages: 2, pageTexts: texts }) });
    await findPdf(documentRef, query);
    assert.match(el(documentRef, "find-count").textContent, expected);
    assert.equal(el(documentRef, "error").hidden, true);
    assert.equal(result.state.pdfDocument.numPages, 2);
  }
});


test("find: closing while extraction is pending prevents stale navigation or counts", async () => {
  const pdfjs = fakePdfjs({ numPages: 3 });
  let release;
  const task = pdfjs.getDocument({});
  const pdf = await task.promise;
  const page = await pdf.getPage(1);
  pdf.getPage = async (number) => ({ ...page,
    getTextContent: () => new Promise((resolve) => { release = resolve; }),
  });
  pdfjs.getDocument = () => task;
  const { documentRef, result } = await start({ pdfjs });
  await fire(el(documentRef, "find-toggle"), "click");
  el(documentRef, "find-input").value = "path";
  const pending = fire(el(documentRef, "find-input"), "input");
  // Let getPage resolve, so the text extraction itself is in flight.
  await Promise.resolve();
  await Promise.resolve();
  await fire(el(documentRef, "find-close"), "click");
  release({ items: [{ str: "path" }] });
  await pending;
  assert.equal(result.state.currentPage, 1);
  assert.equal(el(documentRef, "find-bar").hidden, true);
  assert.deepEqual(result.state.search.matches, []);
});

test("find: clearing a query removes results and reopening preserves a completed search", async () => {
  const { documentRef, result } = await start({ pdfjs: fakePdfjs({ numPages: 1 }) });
  await findPdf(documentRef, "volume");
  assert.equal(el(documentRef, "find-count").textContent, "1 of 2");
  await fire(el(documentRef, "find-close"), "click");
  await fire(el(documentRef, "find-toggle"), "click");
  assert.equal(el(documentRef, "find-count").textContent, "1 of 2");
  el(documentRef, "find-input").value = "";
  await fire(el(documentRef, "find-input"), "input");
  assert.equal(el(documentRef, "find-count").textContent, "");
  assert.equal(el(documentRef, "find-next").disabled, true);
  assert.deepEqual(result.state.search.matches, []);
});

for (const [mode, config] of [["full", makeConfig()], ["minimal", makeConfig(MINIMAL)]]) {
  test(`titles: authored LaTeX reaches the renderer on overview and detail (${mode})`, async () => {
    const label = String.raw`Subpath \(\gamma_{[r,s]}\)`;
    const sections = makeProofSections(PDF_SHA256);
    sections["9/theorem-9.1"][0] = JSON.parse(JSON.stringify(sections["9/theorem-9.1"][0])
      .replace("Theorem 9.1 (Doubling)", label.replaceAll("\\", "\\\\")));
    const rendered = [];
    const renderMath = (tex, doc) => {
      rendered.push(tex);
      const math = doc.createElement("math");
      math.textContent = tex;
      return math;
    };
    const { documentRef } = await start({config, sections, renderMath});
    await turnTo(documentRef, 9);
    assert.ok(rendered.includes(String.raw`\gamma_{[r,s]}`));
    rendered.length = 0;
    await openStep(documentRef, "a");
    await fire(el(documentRef, "step-parent"), "click");
    assert.ok(rendered.includes(String.raw`\gamma_{[r,s]}`));
    assert.equal(el(documentRef, "item-label").children[0].children[1].tagName, "MATH");
  });
}


test("panel divider: resize keys do not turn PDF pages; reading mode hides and restores it", async () => {
  const { documentRef, result } = await start();
  const divider = el(documentRef, 'panel-divider');
  const page = result.state.currentPage;
  await fire(documentRef, 'keydown', { target: divider, key: 'ArrowRight' });
  assert.equal(result.state.currentPage, page);
  await fire(el(documentRef, 'toggle-reading'), 'click');
  assert.equal(divider.hidden, true);
  await fire(el(documentRef, 'toggle-reading'), 'click');
  assert.equal(divider.hidden, false);
});
