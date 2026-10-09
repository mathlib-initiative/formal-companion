/**
 * The overlay viewer's DOM controller, through its injectable seams.
 *
 * Every run here drives `bootstrapViewer` with a fake document built from the
 * real `runtime/viewer/index.html`, a fetch that serves a synthetic
 * `overlay.config.json`, a synthetic `GENERATED.json`, synthetic Literature
 * Links shards and synthetic PDF bytes, and a fake PDF.js. The pure half is
 * covered in viewer-core.test.mjs.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { sha256Hex } from "../runtime/viewer/viewer-core.js";
import { bootstrapViewer, isViewerShortcut, rememberedPage } from "../runtime/viewer/viewer.js";
import {
  FakeOption,
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
  makeGenerated,
  makeShards,
  memoryStorage,
  notFound,
  shardText,
  shellHtml,
  throwingStorage,
} from "./fixtures.mjs";

const PDF_SHA256 = await sha256Hex(PDF_BYTES);
const SHELL = await shellHtml();
const SHARD_URLS = [`${ORIGIN}/annotations/1.jsonl`, `${ORIGIN}/annotations/2.jsonl`];

/** Node has no `Option` constructor; `setOptions` builds its rows with one. */
function withOption(t) {
  const had = "Option" in globalThis;
  globalThis.Option ??= FakeOption;
  t.after(() => {
    if (!had) {
      delete globalThis.Option;
    }
  });
}

function pdfResponse(bytes = PDF_BYTES, { contentType = "application/pdf", ok = true, status = 200 } = {}) {
  return jsonResponse(bytes, { contentType, ok, status });
}

async function start({
  config = makeConfig(),
  shards = makeShards(PDF_SHA256),
  generated = undefined,
  documentRef = fakeDocument(SHELL),
  locationRef = { hash: "" },
  pdf = () => pdfResponse(),
  pdfjs = fakePdfjs(),
  served = {},
  storage = memoryStorage(),
} = {}) {
  const record = generated === undefined ? await makeGenerated(shards) : generated;
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
    for (const [shardPath, records] of Object.entries(shards)) {
      if (url === `${ORIGIN}/${shardPath}`) {
        return jsonResponse(served[shardPath] ?? shardText(records));
      }
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
  });
  return { documentRef, fetched, hashes: urls, pdfjs, result, storage };
}

function el(documentRef, id) {
  return documentRef.elements.get(id);
}

function cardsShown(documentRef) {
  return el(documentRef, "target-groups").children.flatMap((section) => section.children[1].children);
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

test("start-up: the config, the shard list, each shard and the route are fetched", async (t) => {
  withOption(t);
  const { fetched, pdfjs } = await start();
  assert.deepEqual(fetched.map((call) => call.url),
                   ["../overlay.config.json", "../GENERATED.json", ...SHARD_URLS, PDF_ROUTE]);
  for (const call of fetched) {
    assert.deepEqual(call.options, { cache: "no-store", credentials: "same-origin" });
  }
  assert.equal(pdfjs.calls.length, 1);
  assert.equal(pdfjs.calls[0].isEvalSupported, false);
  assert.deepEqual(pdfjs.calls[0].data, PDF_BYTES);
});

test("start-up: the shell's placeholders are replaced from the configuration", async (t) => {
  withOption(t);
  const { documentRef } = await start();
  assert.equal(documentRef.title, "Elementary sets: source overlay (synthetic)");
  assert.equal(el(documentRef, "page-heading").textContent, "Elementary sets: source overlay (synthetic)");
  assert.equal(el(documentRef, "page-lede").textContent, "Regions on the source, declarations in the companion.");
  assert.equal(el(documentRef, "back-link").textContent, "Back to the notes");
  assert.equal(el(documentRef, "back-link").attributes.href, "../../index.html");
  assert.equal(el(documentRef, "workspace").hidden, false);
  assert.equal(el(documentRef, "error").hidden, true);
  assert.equal(el(documentRef, "notice").hidden, true);
  assert.match(el(documentRef, "status").textContent, /^Verified PDF · page 1 of 12$/);
});

test("start-up: refused data leaves an error on the page, not a blank one", async (t) => {
  withOption(t);
  const shards = makeShards(PDF_SHA256);
  for (const [options, pattern] of [
    [{ config: makeConfig({ pdf: { route: "/files/notes.pdf" } }) }, /pdf\.route/],
    [{ generated: null }, /Unable to load \.\.\/GENERATED\.json \(404\)/],
    [{ generated: {} }, /no annotations block/],
    [{ config: makeConfig({ source_id: "src:nowhere" }) }, /No shard holds a record of the PDF "src:nowhere"/],
    [{ served: { "annotations/2.jsonl": "{}\n" } }, /annotations\/2\.jsonl has sha256 [a-f\d]{64}; GENERATED\.json records/],
  ]) {
    const documentRef = fakeDocument(SHELL);
    const generated = options.generated === undefined ? await makeGenerated(shards) : options.generated;
    await assert.rejects(() => start({ ...options, documentRef, generated, shards }), pattern);
    assert.equal(el(documentRef, "error").hidden, false);
    assert.match(el(documentRef, "error").textContent, pattern);
    assert.equal(el(documentRef, "workspace").hidden, true);
    assert.equal(el(documentRef, "status").textContent, "The overlay data was not loaded.");
  }
});

test("start-up: a shard that matches its sha256 but is not JSONL is refused by line", async (t) => {
  withOption(t);
  const shards = makeShards(PDF_SHA256);
  const broken = { ...shards };
  const generated = await makeGenerated(shards);
  generated.annotations.shards[1].sha256 = await sha256Hex(new TextEncoder().encode("{oops\n"));
  await assert.rejects(() => start({ generated, served: { "annotations/2.jsonl": "{oops\n" }, shards: broken }),
                       /annotations\/2\.jsonl:1 is not JSON/);
});

test("start-up: a record that cannot be placed is reported in the page notice", async (t) => {
  withOption(t);
  const shards = makeShards(PDF_SHA256);
  shards["annotations/2.jsonl"][0].location = { page: 5, rect: [0.9, 0.1, 0.4, 0.1] };
  const { documentRef, result } = await start({ shards });
  assert.equal(el(documentRef, "notice").hidden, false);
  assert.match(el(documentRef, "notice").textContent, /^1 region\(s\) could not be placed/);
  assert.equal(result.state.index.pages.has(5), false);
  assert.equal(el(documentRef, "workspace").hidden, false, "the rest of the overlay still opens");
});

// --- the PDF ----------------------------------------------------------------

test("pdf: a hash mismatch stops before PDF.js and says so on the page", async (t) => {
  withOption(t);
  const pdfjs = fakePdfjs();
  const { documentRef, result } = await start({ pdfjs, shards: makeShards(fakeSha256("9")) });
  assert.equal(pdfjs.calls.length, 0, "PDF.js must not see unverified bytes");
  assert.equal(result.state.pdfDocument, null);
  assert.equal(el(documentRef, "workspace").hidden, true);
  assert.match(el(documentRef, "error").textContent, /Loopback PDF SHA-256 is/);
  assert.equal(el(documentRef, "status").textContent, "The verified loopback PDF was not opened.");
});

test("pdf: a 404, a wrong media type and a wrong byte count are all refused", async (t) => {
  withOption(t);
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

// --- regions, items and cards ----------------------------------------------

test("rendered: selecting a region shows its item's cards, grouped in role order", async (t) => {
  withOption(t);
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0001");
  assert.equal(el(documentRef, "selection").hidden, false);
  assert.equal(el(documentRef, "selection-empty").hidden, true);
  assert.equal(el(documentRef, "item-label").textContent, "Theorem 1.1");
  assert.equal(el(documentRef, "item-locator").textContent, "p. 2 (physical)");
  assert.equal(el(documentRef, "target-summary").textContent,
               "Formalised in this companion: a key declaration states it.");
  assert.deepEqual(el(documentRef, "target-groups").children.map((section) => section.children[0].textContent),
                   ["key", "attributed", "support", "example", "helper"]);
  const cards = cardsShown(documentRef);
  assert.deepEqual(cards.map((card) => card.children[0].textContent), [
    "Box.volume_nonneg", "Box.volume_mono", "Set.inter_sub", "example:0123456789ab", "Set.union_comm",
  ]);
  assert.equal(cards[0].children[0].tagName, "A");
  assert.equal(cards[0].children[0].attributes.href, `${ORIGIN}/Notes/Box/index.html#Notes___Box___volume_nonneg`);
  assert.equal(cards[3].children[0].tagName, "SPAN", "an unlinked card is not a link");
  assert.ok(cards[0].children.some((child) => child.className === "target-snippet"));
  // The region is drawn through the displayed page: a quarter of 600 pt across, an eighth of 800 down.
  const polygon = el(documentRef, "overlay").children.find((child) => child.dataset.regionId === "loc:0001");
  assert.equal(polygon.attributes.points, "150,100 450,100 450,200 150,200");
});

test("rendered: verdict badges mark a repaired declaration faithful and an old one superseded", async (t) => {
  withOption(t);
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0001");
  const badges = cardsShown(documentRef)
    .flatMap((card) => card.children)
    .filter((child) => String(child.className).includes("verdict-badge"))
    .map((child) => `${child.textContent}|${child.className}`);
  assert.deepEqual(badges, [
    "faithful|target-badge verdict-badge verdict-faithful",
    "statement deviation, superseded|target-badge verdict-badge verdict-deviation",
  ]);
  const [note] = el(documentRef, "verdict-notes").children;
  assert.match(note.className, /verdict-fixed/);
  assert.equal(note.textContent, "Faithfulness: fixed: statement deviation (medium). The box bound was stated "
               + "for closed boxes only. [fixed; fixed, PR #7]");
  assert.equal(note.title, "Notes/Box.lean:12\nsource page 2");
});

test("rendered: a status states what applies, with its class and its note", async (t) => {
  withOption(t);
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0002");
  assert.equal(el(documentRef, "item-label").textContent, "Definition 2.1");
  assert.equal(el(documentRef, "target-summary").textContent,
               "Supported only by declarations that state a neighbouring item.");
  assert.equal(el(documentRef, "target-summary").className, "target-summary coverage-summary coverage-support_only");
  assert.equal(el(documentRef, "coverage-note").hidden, false);
  assert.equal(el(documentRef, "coverage-note").textContent,
               "The companion states the neighbouring box lemma, not this definition.");
  assert.equal(el(documentRef, "fallback-note").hidden, true);
  assert.deepEqual(cardsShown(documentRef).map((card) => card.children[0].title), ["Notes.Set.inter_sub"]);
});

test("rendered: an item that links nothing says so and names its cluster", async (t) => {
  withOption(t);
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0003");
  assert.equal(el(documentRef, "item-label").textContent, "Exercise 3.1");
  assert.equal(el(documentRef, "target-summary").textContent, "No declaration is linked to this item.");
  assert.equal(el(documentRef, "fallback-note").hidden, false);
  assert.equal(el(documentRef, "fallback-note").textContent,
               "No declaration is linked to this item; its ledger cluster: cluster:sets.");
  assert.deepEqual(cardsShown(documentRef), []);
});

test("rendered: a region with two items offers a choice, and the choice switches the cards", async (t) => {
  withOption(t);
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0004");
  const choices = el(documentRef, "item-choices");
  assert.equal(choices.hidden, false);
  assert.deepEqual(choices.children.map((button) => button.textContent), ["A remark on unions", "Theorem 1.1"]);
  assert.equal(el(documentRef, "item-label").textContent, "A remark on unions");
  assert.deepEqual(cardsShown(documentRef).map((card) => card.children[0].title), ["Notes.Internal.aux"]);
  await fire(choices.children[1], "click");
  assert.equal(el(documentRef, "item-label").textContent, "Theorem 1.1");
  assert.equal(cardsShown(documentRef).length, 5);
  assert.deepEqual(choices.children.map((button) => button.attributes["aria-pressed"]), ["false", "true"]);
});

test("rendered: the role filter and the search narrow the card list", async (t) => {
  withOption(t);
  const { documentRef, result } = await start();
  await selectPageRegion(documentRef, result.state, "loc:0001");
  const role = el(documentRef, "role-filter");
  assert.deepEqual(role.children.map((option) => option.value),
                   ["", "attributed", "example", "helper", "key", "support"]);
  role.value = "support";
  await fire(role, "input");
  assert.deepEqual(cardsShown(documentRef).map((card) => card.children[0].title), ["Notes.Set.inter_sub"]);
  role.value = "";
  const search = el(documentRef, "target-filter");
  search.value = "volume";
  await fire(search, "input");
  assert.deepEqual(cardsShown(documentRef).map((card) => card.children[0].title),
                   ["Notes.Box.volume_nonneg", "Notes.Box.volume_mono"]);
  assert.equal(el(documentRef, "target-page").textContent, "Page 1 of 1");
});

// --- navigation and the remembered page -------------------------------------

test("navigation: the buttons and the page input move, and clear the selection", async (t) => {
  withOption(t);
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

test("navigation: the page is remembered under the configured storage key", async (t) => {
  withOption(t);
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

test("navigation: a storage that throws on every access never breaks the viewer", async (t) => {
  withOption(t);
  const storage = throwingStorage();
  assert.equal(rememberedPage("notes-overlay-page", { locationRef: { hash: "" }, storage }), null);
  const { documentRef, result } = await start({ storage });
  assert.equal(result.state.currentPage, 1);
  await fire(el(documentRef, "next-page"), "click");
  assert.equal(result.state.currentPage, 2, "writing the remembered page is best-effort");
  assert.equal(el(documentRef, "error").hidden, true);
});

// --- the keyboard -----------------------------------------------------------

test("keyboard: the shortcuts work bare and are ignored with a modifier or in an input", async (t) => {
  withOption(t);
  const { documentRef, result } = await start();
  await fire(documentRef, "keydown", { key: "ArrowRight" });
  assert.equal(result.state.currentPage, 2);
  await fire(documentRef, "keydown", { key: "ArrowLeft" });
  assert.equal(result.state.currentPage, 1);
  await fire(documentRef, "keydown", { key: "+" });
  assert.equal(result.state.zoom, 110);
  await fire(documentRef, "keydown", { key: "-" });
  assert.equal(result.state.zoom, 100);
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

test("keyboard: the shortcut test is the predicate's own, so both halves agree", () => {
  assert.equal(isViewerShortcut({ key: "ArrowRight", target: {} }), true);
  assert.equal(isViewerShortcut({ ctrlKey: true, key: "ArrowRight", target: {} }), false);
  assert.equal(isViewerShortcut({ key: "ArrowRight", metaKey: true, target: {} }), false);
  assert.equal(isViewerShortcut({ key: "r", target: { tagName: "INPUT" } }), false);
  assert.equal(isViewerShortcut(null), false);
});

test("escape clears the selected region and Enter on a region selects it", async (t) => {
  withOption(t);
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

test("shell: a back link the config spells as javascript: never reaches the header", async (t) => {
  withOption(t);
  const { documentRef } = await start({ config: makeConfig({ back_link: { href: "javascript:alert(1)", text: "Back" } }) });
  assert.equal(el(documentRef, "back-link").attributes.href, "../../index.html");
  assert.equal(el(documentRef, "back-link").textContent, "Back");
  const offsite = await start({ config: makeConfig({ back_link: { href: "//notes.example.invalid/x.html", text: "Back" } }) });
  assert.equal(el(offsite.documentRef, "back-link").attributes.href, "../../index.html");
});

test("shell: the title and heading are placeholders the configuration replaces", async (t) => {
  withOption(t);
  const placeholder = /Source and formalization overlay/;
  assert.match(SHELL, placeholder);
  const { documentRef } = await start({ config: makeConfig({ lede: "", title: "Another title" }) });
  assert.equal(documentRef.title, "Another title");
  assert.equal(el(documentRef, "page-heading").textContent, "Another title");
  assert.equal(el(documentRef, "page-lede").hidden, true);
});
