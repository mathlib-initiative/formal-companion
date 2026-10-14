/**
 * The overlay viewer's DOM controller.
 *
 * It fetches `../overlay.config.json`, then `../GENERATED.json`, then the
 * annotation bundle it records (checked against its recorded sha256): every
 * Literature Links annotation file with its path, in one fetch. It takes
 * every per-companion value from them: the page
 * title and heading, the back link, the PDF route and its sha256, the pages,
 * the regions, the items, the cards, the verdicts. The pure half (the bundle,
 * the records, geometry, href rules, hashing) is in viewer-core.js, so
 * `node --test` reaches it without a DOM; this file keeps the injectable
 * `fetch` / `document` / `storage` seams the tests drive it through.
 *
 * Security (docs/overlay.md section 4): every string from those files reaches
 * the DOM through `textContent` or an attribute setter, never `innerHTML`; an
 * `href` is used only when a record's target resolves to an `https:` URL or a
 * page of this site; the PDF is requested only from `config.pdf.route`;
 * PDF.js runs with `isEvalSupported: false`.
 */
import {
  CARD_PAGE_SIZE,
  CONFIG_URL,
  GENERATED_URL,
  MAX_JSON_BYTES,
  VERDICT_LABELS,
  buildIndex,
  cardsMatching,
  coverageSummary,
  coverageWord,
  declarationVerdictDisplay,
  heroCard,
  labelParts,
  leanTokens,
  locatorTail,
  pageSummaryText,
  paginateCards,
  parseBundle,
  preferredItemId,
  quadToViewport,
  readConfig,
  readAnnotationsBlock,
  regionCoverage,
  regionVerdict,
  reviewFooter,
  sha256Hex,
  splitName,
  verdictFooter,
  verdictLine,
  verifyPdfBytes,
} from "./viewer-core.js";

export const PDF_MEDIA_TYPE = "application/pdf";

/** The hash the viewer writes and reads back for the current page. */
const PAGE_HASH = /^#page=(\d{1,6})$/;

function decodeUtf8(bytes, label) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (error) {
    throw new Error(`${label} is not valid UTF-8 (${error.message}).`);
  }
}

function parseJsonBytes(bytes, label) {
  try {
    return JSON.parse(decodeUtf8(bytes, label));
  } catch (error) {
    throw new Error(`${label} is not valid UTF-8 JSON (${error.message}).`);
  }
}

/** A same-origin GET with no cache and a hard size cap. */
export async function checkedFetch(url, fetchImpl, limit = MAX_JSON_BYTES) {
  if (typeof fetchImpl !== "function") {
    throw new Error("Fetch is unavailable; the viewer cannot load its data.");
  }
  const response = await fetchImpl(url, { cache: "no-store", credentials: "same-origin" });
  if (!response?.ok) {
    throw new Error(`Unable to load ${url} (${response?.status ?? "no response"}).`);
  }
  const declared = Number(response.headers?.get?.("Content-Length"));
  if (Number.isFinite(declared) && declared > limit) {
    throw new Error(`${url} declares ${declared} bytes, over the ${limit}-byte cap.`);
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > limit) {
    throw new Error(`${url} is ${bytes.byteLength} bytes, over the ${limit}-byte cap.`);
  }
  return bytes;
}

/** Read `../overlay.config.json`. */
export async function loadConfig(fetchImpl = globalThis.fetch, url = CONFIG_URL) {
  return readConfig(parseJsonBytes(await checkedFetch(url, fetchImpl), "overlay.config.json"));
}

/**
 * Read `GENERATED.json`, fetch the bundle it records (beside it) and check its
 * sha256, then index the records, each with the URL of its own file under the
 * site root: Literature Links resolves a relative target against the file
 * that holds the annotation. A bundle that does not match its recorded sha256
 * is a refusal: the page and the data it was generated with are out of step.
 */
export async function loadAnnotations(config, fetchImpl = globalThis.fetch, cryptoProvider = globalThis.crypto,
                                      pageUrl = globalThis.location?.href, generatedUrl = GENERATED_URL) {
  const block = readAnnotationsBlock(parseJsonBytes(await checkedFetch(generatedUrl, fetchImpl), "GENERATED.json"));
  const bundleUrl = new URL(block.bundle.path, new URL(generatedUrl, pageUrl)).href;
  const bytes = await checkedFetch(bundleUrl, fetchImpl);
  const actual = await sha256Hex(bytes, cryptoProvider);
  if (actual !== block.bundle.sha256) {
    throw new Error(`${block.bundle.path} has sha256 ${actual}; GENERATED.json records ${block.bundle.sha256}.`);
  }
  // siteRoot is relative to the viewer's own directory, where pageUrl is.
  const base = new URL(config.siteRoot, pageUrl).href;
  const files = parseBundle(decodeUtf8(bytes, block.bundle.path), block)
    .map(({ annotation, path }) => ({ path, records: [annotation], url: new URL(path, base).href }));
  return { files, index: buildIndex(files, config, { pageOrigin: new URL(pageUrl).origin }) };
}

/**
 * Fetch the PDF from the configured loopback route and verify its bytes against
 * the sha256 its source pins before anything renders them.
 */
export async function loadPdfBytes(config, index, fetchImpl = globalThis.fetch,
                                   cryptoProvider = globalThis.crypto) {
  if (typeof fetchImpl !== "function") {
    throw new Error("Fetch is unavailable; the verified loopback PDF cannot be loaded.");
  }
  const response = await fetchImpl(config.pdfRoute, {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response?.ok) {
    throw new Error(
      `Unable to load the verified loopback PDF (${response?.status ?? "no response"}).`,
    );
  }
  const mediaType = response.headers?.get?.("Content-Type")?.split(";", 1)[0].trim().toLowerCase();
  if (mediaType !== PDF_MEDIA_TYPE) {
    throw new Error(`The loopback PDF response has unexpected media type ${mediaType || "missing"}.`);
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  const declared = index.bytes;
  if (Number.isInteger(declared) && bytes.byteLength !== declared) {
    throw new Error(`The loopback PDF is ${bytes.byteLength} bytes; the source record says ${declared}.`);
  }
  const verification = await verifyPdfBytes(bytes, index.sha256, cryptoProvider);
  if (!verification.ok) {
    throw new Error(
      `Loopback PDF SHA-256 is ${verification.actual}; expected ${verification.expected}.`,
    );
  }
  return { bytes, verification };
}

function element(documentRef, id) {
  const value = documentRef.getElementById(id);
  if (!value) {
    throw new Error(`Viewer shell is missing #${id}.`);
  }
  return value;
}

/**
 * The remembered page: the `#page=` hash first, then `storage_key`. Every
 * storage access is wrapped, because a browser in private mode throws on both
 * reading and writing it and a remembered page is never worth a blank viewer.
 */
export function rememberedPage(storageKey, { storage, locationRef } = {}) {
  const fromHash = PAGE_HASH.exec(locationRef?.hash ?? "");
  if (fromHash) {
    return Number(fromHash[1]);
  }
  try {
    const value = Number(storage?.getItem(storageKey));
    return Number.isInteger(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

export function rememberPage(storageKey, page, { storage, historyRef } = {}) {
  try {
    historyRef?.replaceState?.(null, "", `#page=${page}`);
  } catch {
    // The History API may be unavailable; the storage fallback below still applies.
  }
  try {
    storage?.setItem(storageKey, String(page));
  } catch {
    // Storage may be blocked (private mode); remembering the page is best-effort.
  }
}

/** A keydown the viewer acts on: no modifier held, and focus outside an input. */
export function isViewerShortcut(event) {
  if (!event || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) {
    return false;
  }
  return !["INPUT", "SELECT", "TEXTAREA"].includes(event.target?.tagName);
}


/** Statements longer than this many lines are clamped, with a "Show all" button. */
const STATEMENT_LINES = 14;
/** The "Also states it" list shows this many rows before "Show N more". */
const ALSO_ROWS = 10;
/** Above this many cards, the item view offers a filter by name. */
const FILTER_ABOVE = 20;
/** Room the floating PDF controls take at the bottom of the PDF pane. */
const CONTROLS_ROOM = 64;

const SVG_NS = "http://www.w3.org/2000/svg";

/** The badges a card shows; "fallback" and "unlinked" are said by its source link instead. */
function shownBadges(card) {
  return card.badges.filter((badge) => badge !== "fallback" && badge !== "unlinked");
}

function setClass(node, name, on) {
  if (node?.classList?.toggle) {
    node.classList.toggle(name, Boolean(on));
  }
}

export async function bootstrapViewer({
  configUrl = CONFIG_URL,
  cryptoProvider = globalThis.crypto,
  documentRef = globalThis.document,
  fetchImpl = globalThis.fetch,
  historyRef = globalThis.history,
  locationRef = globalThis.location,
  generatedUrl = GENERATED_URL,
  pageUrl = globalThis.location?.href,
  pdfjsLoader = () => import("../vendor/pdfjs/pdf.min.mjs"),
  windowRef = globalThis,
  storage = (() => {
    try {
      return globalThis.localStorage ?? null;
    } catch {
      return null;
    }
  })(),
} = {}) {
  const ui = Object.fromEntries(
    [
      "back-link", "page-heading", "page-lede",
      "error", "status", "status-glyph", "status-chip", "notice", "workspace",
      "previous-page", "next-page", "page-number", "page-count",
      "zoom-out", "zoom-in", "zoom-value", "rotate-right", "toggle-faithfulness",
      "page-scroller", "page-stage", "pdf-canvas", "overlay", "region-bar", "region-peek",
      "details", "selection-empty", "index-heading", "index-meta", "page-index", "legend",
      "verdict-legend", "index-footer",
      "selection", "back-to-page", "previous-item", "next-item", "item-position", "item-choices",
      "item-head", "item-label", "item-locator", "target-summary", "coverage-note", "verdict-notes",
      "hero", "also-section", "also-count", "also-list", "also-more",
      "related-section", "related-count", "related-list", "target-filter",
      "related-pager", "previous-targets", "next-targets", "target-page", "provenance-note",
    ].map((id) => [id, element(documentRef, id)]),
  );
  const state = {
    alsoAll: false,
    boxes: new Map(),
    cardPage: 1,
    config: null,
    currentPage: 1,
    faithfulness: false,
    index: null,
    openCards: new Set(),
    pdfDocument: null,
    region: null,
    renderEpoch: 0,
    renderTask: null,
    rotation: 0,
    selected: null,
    zoom: 100,
  };
  let bodyCount = 0;

  /** An element with a class and text, both optional; every string goes in as text. */
  function make(tag, className, value) {
    const node = documentRef.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (value !== undefined) {
      node.textContent = value;
    }
    return node;
  }

  function showError(error) {
    ui.error.textContent = error instanceof Error ? error.message : String(error);
    ui.error.hidden = false;
  }

  function clearError() {
    ui.error.hidden = true;
    ui.error.textContent = "";
  }

  /** The status chip: "loading", "ok" or "bad", and its one line of text. */
  function setStatus(kind, message) {
    ui.status.textContent = message;
    ui["status-glyph"].textContent = kind === "ok" ? "✓" : kind === "bad" ? "!" : "";
    ui["status-chip"].className = `status-chip is-${kind}`;
  }

  function setNotice(message) {
    ui.notice.hidden = !message;
    ui.notice.textContent = message ? `ⓘ ${message}` : "";
  }

  function applyConfig(config) {
    // Every per-companion string on the shell, written as text, never as markup.
    documentRef.title = config.title;
    ui["page-heading"].textContent = config.title;
    ui["page-heading"].title = config.title;
    ui["page-lede"].textContent = config.lede;
    ui["page-lede"].hidden = !config.lede;
    ui["back-link"].textContent = config.backLink.text;
    ui["back-link"].setAttribute("href", config.backLink.href);
  }

  // --- the page: its regions and their items, in reading order ---------------

  function pageRegions() {
    return state.index?.pages.get(state.currentPage) ?? [];
  }

  /**
   * Every item on the page once, with the first region that shows it: the
   * page index's rows and the order the stepper walks.
   */
  function pageEntries() {
    const seen = new Set();
    return pageRegions().flatMap((region) => region.itemIds.filter((itemId) => !seen.has(itemId)
      && seen.add(itemId)).map((itemId) => ({ item: state.index.items.get(itemId), region })));
  }

  function regionItems(region) {
    return region.itemIds.map((itemId) => state.index.items.get(itemId));
  }

  /** The stroke of a region's items: solid, dashed or dotted, from the best-covered item. */
  function coverageOfRegion(region) {
    const kinds = regionItems(region).map(regionCoverage);
    return ["formalized", "partial"].find((kind) => kinds.includes(kind)) ?? "quiet";
  }

  function polygonsOf(regionId) {
    return [...(ui.overlay.children ?? [])].filter((polygon) => polygon.dataset?.regionId === regionId);
  }

  /** Light a region from the panel, or a row from the page; null puts them all out. */
  function setHot(regionId) {
    for (const polygon of ui.overlay.children ?? []) {
      setClass(polygon, "is-hot", regionId && polygon.dataset?.regionId === regionId);
    }
    for (const row of ui["page-index"].children ?? []) {
      const button = row.children?.[0];
      setClass(button, "is-hot", regionId && button?.dataset?.regionId === regionId);
    }
  }

  /** A declaration name: its namespace faint (fainter still when it repeats the row above), its leaf strong. */
  function nameNode(name, className, previous = null) {
    const node = make("span", className);
    const [namespace, leaf] = splitName(name);
    if (namespace) {
      const part = make("span", namespace === previous ? "ns ditto" : "ns");
      for (const piece of namespace.split(/(?<=\.)/)) {
        part.append(piece, make("wbr"));
      }
      node.append(part);
    }
    node.append(make("span", "leaf", leaf));
    return node;
  }

  /** A Lean statement, lightly tinted; the text is the snippet's, exactly. */
  function leanNode(source, tag = "pre", className = "lean") {
    const node = make(tag, className);
    for (const [kind, value] of leanTokens(source)) {
      node.append(kind === "tx" ? value : make("span", `tk-${kind}`, value));
    }
    return node;
  }

  /** A statement block, clamped past STATEMENT_LINES lines with a button to show the rest. */
  function statementNodes(source) {
    const pre = leanNode(source);
    if (source.split("\n").length <= STATEMENT_LINES) {
      return [pre];
    }
    setClass(pre, "is-clamped", true);
    const button = make("button", "text-button show-all", "Show all");
    button.type = "button";
    button.setAttribute("aria-expanded", "false");
    let clamped = true;
    button.addEventListener("click", () => {
      clamped = !clamped;
      setClass(pre, "is-clamped", clamped);
      button.setAttribute("aria-expanded", String(!clamped));
      button.textContent = clamped ? "Show all" : "Show less";
    });
    return [pre, button];
  }

  /** "View in source ↗", its module-page fallback, or plain text when nothing is rendered. */
  function sourceLink(card) {
    if (!card.href) {
      return make("span", "no-link", "No rendered page");
    }
    const link = make("a", "source-link", card.resolution === "fallback"
      ? "View in source · module page" : "View in source ↗");
    link.setAttribute("href", card.href);
    link.title = card.resolution === "fallback" ? card.fallbackLabel || card.fullName : card.fullName;
    return link;
  }

  /** A card's declaration verdict, when verdicts are shown: a dot and its label, the note as title. */
  function cardVerdict(card, className = "card-verdict") {
    if (!state.faithfulness || !card.verdict) {
      return null;
    }
    const display = declarationVerdictDisplay(card.verdict.verdict, card.verdict.status);
    const mark = make("span", `${className} verdict-badge ${display.className}`, display.label);
    mark.title = card.verdict.note;
    return mark;
  }

  // --- the page index (nothing selected) --------------------------------------

  function legendEntry(className, label) {
    const entry = make("span", "legend-entry");
    entry.append(make("i", className), label);
    return entry;
  }

  function renderIndex() {
    const entries = pageEntries();
    const items = entries.map((entry) => entry.item);
    ui["index-heading"].textContent = `Page ${state.currentPage}`;
    ui["index-meta"].textContent = pageSummaryText(items);
    ui["page-index"].setAttribute("aria-label", `Passages on page ${state.currentPage}`);
    ui["page-index"].replaceChildren();
    ui["page-index"].hidden = !entries.length;
    ui.legend.hidden = !entries.length;
    for (const { item, region } of entries) {
      const row = make("button", "index-row");
      row.type = "button";
      row.dataset.regionId = region.id;
      row.dataset.itemId = item.id;
      row.append(make("span", `dot coverage-${item.coverage || "unknown"}`));
      const cell = make("span", "index-label");
      const label = make("span", "index-title", labelParts(item.label).title);
      label.title = item.label;
      cell.append(label);
      const verdict = state.faithfulness ? regionVerdict([item]) : null;
      if (verdict) {
        const dot = make("span", `dot verdict-dot verdict-${verdict}`);
        dot.title = VERDICT_LABELS[verdict] ?? verdict;
        cell.append(dot);
      }
      row.append(cell);
      const hero = heroCard(item);
      const right = make("span", hero ? "index-decl" : "index-decl is-empty");
      if (hero) {
        right.append(nameNode(hero.name, "decl"));
        if (item.cards.length > 1) {
          right.append(make("span", "more", ` +${item.cards.length - 1}`));
        }
      } else {
        right.textContent = item.cards.length ? coverageWord(item.coverage) || "No key declaration"
          : "No declaration linked";
      }
      row.append(right);
      row.addEventListener("click", () => selectRegion(region, item.id));
      row.addEventListener("mouseenter", () => setHot(region.id));
      row.addEventListener("mouseleave", () => setHot(null));
      row.addEventListener("focus", () => setHot(region.id));
      row.addEventListener("blur", () => setHot(null));
      const li = make("li");
      li.append(row);
      ui["page-index"].append(li);
    }
    ui.legend.replaceChildren(
      legendEntry("swatch formalized", "Formalized"),
      legendEntry("swatch partial", "Partial"),
      legendEntry("swatch quiet", "No key declaration"),
    );
    const kinds = state.faithfulness
      ? [...new Set(items.map((item) => regionVerdict([item])).filter(Boolean))] : [];
    ui["verdict-legend"].replaceChildren(...kinds.map((kind) => {
      const entry = make("span", "legend-entry");
      entry.append(make("span", `dot verdict-dot verdict-${kind}`), VERDICT_LABELS[kind] ?? kind);
      return entry;
    }));
    ui["verdict-legend"].hidden = !kinds.length;
    const footer = state.faithfulness ? verdictFooter(items) : "";
    ui["index-footer"].textContent = footer;
    ui["index-footer"].hidden = !footer;
  }

  // --- the item view -----------------------------------------------------------

  function renderItemChoices(region) {
    ui["item-choices"].replaceChildren();
    const items = region ? region.itemIds : [];
    ui["item-choices"].hidden = items.length < 2;
    if (items.length < 2) {
      return;
    }
    for (const itemId of items) {
      const item = state.index.items.get(itemId);
      const button = make("button", "item-choice", labelParts(item.label).title);
      button.type = "button";
      button.title = item.label;
      button.dataset.itemId = itemId;
      button.setAttribute("aria-pressed", String(state.selected?.id === itemId));
      button.addEventListener("click", () => selectItem(itemId));
      ui["item-choices"].append(button);
    }
  }

  function renderNavStrip(item) {
    const entries = pageEntries();
    const at = entries.findIndex((entry) => entry.item.id === item.id);
    ui["back-to-page"].textContent = `← Page ${state.currentPage}`;
    ui["item-position"].textContent = at >= 0 ? `${at + 1} of ${entries.length}` : "";
    ui["previous-item"].disabled = at <= 0;
    ui["next-item"].disabled = at < 0 || at >= entries.length - 1;
  }

  function stepItem(delta) {
    const entries = pageEntries();
    const at = entries.findIndex((entry) => entry.item.id === state.selected?.id);
    const next = entries[at + delta];
    if (at >= 0 && next) {
      selectRegion(next.region, next.item.id);
    }
  }

  function renderHead(item) {
    const parts = labelParts(item.label);
    ui["item-label"].replaceChildren(make("span", "item-title", parts.title));
    if (parts.subtitle) {
      ui["item-label"].append(make("span", "item-subtitle", parts.subtitle));
    }
    const word = coverageWord(item.coverage);
    ui["target-summary"].className = item.coverage
      ? `target-summary coverage-${item.coverage}` : "target-summary";
    ui["target-summary"].textContent = word;
    ui["target-summary"].title = coverageSummary(item.coverage);
    ui["target-summary"].hidden = !word;
    const tail = locatorTail(item.label, item.locator);
    ui["item-locator"].textContent = tail;
    ui["item-locator"].title = item.locator;
    ui["item-locator"].hidden = !tail;
    ui["coverage-note"].hidden = !item.coverageNote;
    ui["coverage-note"].textContent = item.coverageNote;
  }

  function renderVerdictNotes(item) {
    ui["verdict-notes"].replaceChildren();
    for (const verdict of state.faithfulness ? item.verdicts : []) {
      const shown = verdictLine(verdict);
      const line = make("p", "verdict-line");
      line.title = shown.title;
      line.append(make("span", `verdict-badge ${shown.className}`, shown.label));
      if (shown.summary) {
        line.append(make("span", "verdict-summary", shown.summary));
      }
      for (const quiet of [shown.resolution, shown.statusWord]) {
        if (quiet) {
          line.append(make("span", "faint", quiet));
        }
      }
      ui["verdict-notes"].append(line);
    }
    ui["verdict-notes"].hidden = !ui["verdict-notes"].children?.length;
  }

  /** The eyebrow of the hero: kind, verdict and badges on the left, the source link on the right. */
  function eyebrow(card) {
    const row = make("div", "eyebrow");
    const left = make("span", "eyebrow-left");
    left.append(make("span", "kind", card.kind || "declaration"));
    const verdict = cardVerdict(card);
    if (verdict) {
      left.append(verdict);
    }
    for (const badge of shownBadges(card)) {
      left.append(make("span", badge.startsWith("sorry") ? "tag is-sorry" : "tag", badge));
    }
    row.append(left, sourceLink(card));
    return row;
  }

  function renderHero(item) {
    ui.hero.replaceChildren();
    const hero = heroCard(item);
    if (hero) {
      const name = nameNode(hero.name, "decl-name");
      name.title = hero.fullName;
      ui.hero.append(eyebrow(hero), name);
      if (hero.snippet?.text) {
        ui.hero.append(...statementNodes(hero.snippet.text));
      }
    } else if (item.cards.length) {
      ui.hero.append(make("p", "empty-line", "No key declaration states this passage."));
    } else {
      ui.hero.append(make("p", "empty-line is-strong", "No Lean declaration is linked to this passage."));
      if (item.clusterIds.length) {
        const line = make("p", "cluster-line", "Ledger cluster ");
        line.append(make("code", "cluster-ids", item.clusterIds.join(", ")));
        ui.hero.append(line);
      }
    }
  }

  /** One card as a row: a button that opens its statement, and the statement's body below it. */
  function cardRow(card, { previous, preview, related }) {
    const li = make("li", "row");
    const button = make("button", "row-button");
    button.type = "button";
    const bodyId = `card-body-${++bodyCount}`;
    button.setAttribute("aria-controls", bodyId);
    const open = state.openCards.has(card.id);
    button.setAttribute("aria-expanded", String(open));
    const top = make("span", "row-top");
    const name = nameNode(card.name, "row-name", previous);
    name.title = card.fullName;
    const kind = make("span", "row-kind");
    if (state.faithfulness && card.verdict) {
      const display = declarationVerdictDisplay(card.verdict.verdict, card.verdict.status);
      kind.append(make("span", `dot verdict-dot ${display.className}`));
    }
    kind.append(related ? [card.role, card.kind].filter(Boolean).join(" · ") : card.kind);
    top.append(name, kind);
    button.append(top);
    let previewNode = null;
    if (preview && card.snippet?.text) {
      previewNode = leanNode(card.snippet.text, "span", "row-preview");
      previewNode.hidden = open;
      button.append(previewNode);
    }
    const body = make("div", "row-body");
    body.id = bodyId;
    body.hidden = !open;
    let built = false;
    const build = () => {
      if (built) {
        return;
      }
      built = true;
      if (card.snippet?.text) {
        body.append(...statementNodes(card.snippet.text));
      }
      const foot = make("div", "row-foot");
      const meta = make("span", "row-meta");
      const words = [card.relation, ...shownBadges(card)].filter(Boolean);
      if (words.length) {
        meta.append(words.join(" · "));
      }
      const verdict = cardVerdict(card);
      if (verdict) {
        meta.append(verdict);
      }
      foot.append(meta, sourceLink(card));
      body.append(foot);
    };
    if (open) {
      build();
    }
    button.addEventListener("click", () => {
      const opening = !state.openCards.has(card.id);
      if (opening) {
        state.openCards.add(card.id);
        build();
      } else {
        state.openCards.delete(card.id);
      }
      button.setAttribute("aria-expanded", String(opening));
      body.hidden = !opening;
      if (previewNode) {
        previewNode.hidden = opening;
      }
      setClass(li, "is-open", opening);
    });
    setClass(li, "is-open", open);
    li.append(button, body);
    return li;
  }

  function fillRows(list, cards, options) {
    list.replaceChildren();
    let previous = null;
    for (const card of cards) {
      list.append(cardRow(card, { ...options, previous }));
      previous = splitName(card.name)[0] || null;
    }
  }

  function renderCards() {
    const item = state.selected;
    if (!item) {
      return;
    }
    const filtered = item.cards.length > FILTER_ABOVE;
    const query = filtered ? ui["target-filter"].value : "";
    const hero = heroCard(item);
    const keys = item.cards.filter((card) => card.role === "key" && card !== hero);
    const related = item.cards.filter((card) => card.role !== "key");
    const shownKeys = cardsMatching(keys, { query });
    const shownRelated = cardsMatching(related, { query });

    ui["also-section"].hidden = !keys.length;
    ui["also-count"].textContent = String(query ? shownKeys.length : keys.length);
    const capped = state.alsoAll ? shownKeys : shownKeys.slice(0, ALSO_ROWS);
    fillRows(ui["also-list"], capped, { preview: true, related: false });
    const hidden = shownKeys.length - capped.length;
    ui["also-more"].hidden = hidden <= 0;
    ui["also-more"].textContent = hidden > 0 ? `Show ${hidden} more` : "";

    ui["related-section"].hidden = !related.length && !filtered;
    ui["related-count"].textContent = String(query ? shownRelated.length : related.length);
    ui["target-filter"].hidden = !filtered;
    const page = paginateCards(shownRelated, state.cardPage, CARD_PAGE_SIZE);
    state.cardPage = page.page;
    fillRows(ui["related-list"], page.items, { preview: false, related: true });
    ui["related-pager"].hidden = page.pageCount <= 1;
    const first = (page.page - 1) * CARD_PAGE_SIZE + 1;
    ui["target-page"].textContent = page.total
      ? `${first}–${first + page.items.length - 1} of ${page.total}` : "";
    ui["previous-targets"].disabled = page.page <= 1;
    ui["next-targets"].disabled = page.page >= page.pageCount;
  }

  function renderFooter(item) {
    const footer = reviewFooter(item, { faithfulness: state.faithfulness });
    ui["provenance-note"].textContent = footer;
    ui["provenance-note"].hidden = !footer;
  }

  function renderItem(item) {
    renderNavStrip(item);
    renderHead(item);
    renderVerdictNotes(item);
    renderHero(item);
    renderCards();
    renderFooter(item);
  }

  // --- selection -----------------------------------------------------------------

  function selectRegion(region, itemId = null) {
    state.region = region;
    for (const polygon of ui.overlay.children ?? []) {
      polygon.setAttribute("aria-pressed", String(region?.id === polygon.dataset?.regionId));
    }
    setClass(ui.overlay, "has-selection", region);
    hidePeek();
    renderItemChoices(region);
    selectItem(region ? itemId ?? preferredItemId(region, state.index.items) : null);
    updateBar();
    if (region) {
      scrollRegionIntoView(region);
    }
  }

  function selectItem(itemId) {
    const item = itemId ? state.index.items.get(itemId) ?? null : null;
    state.selected = item;
    state.cardPage = 1;
    state.alsoAll = false;
    state.openCards = new Set();
    ui["selection-empty"].hidden = Boolean(item);
    ui.selection.hidden = !item;
    for (const button of ui["item-choices"].children ?? []) {
      button.setAttribute?.("aria-pressed", String(button.dataset?.itemId === itemId));
    }
    ui["target-filter"].value = "";
    if (item) {
      renderItem(item);
    } else {
      renderIndex();
    }
    ui.details.scrollTop = 0;
  }

  // --- the page's marks: the margin bar, the peek, the scroll ------------------

  function updateBar() {
    const box = state.region ? state.boxes.get(state.region.id) : null;
    ui["region-bar"].hidden = !box;
    if (box) {
      ui["region-bar"].style.top = `${box.y}px`;
      ui["region-bar"].style.height = `${box.height}px`;
    }
  }

  function hidePeek() {
    ui["region-peek"].hidden = true;
  }

  function showPeek(region) {
    const box = state.boxes.get(region.id);
    if (!box || state.region?.id === region.id) {
      hidePeek();
      return;
    }
    const item = state.index.items.get(preferredItemId(region, state.index.items));
    const peek = ui["region-peek"];
    peek.replaceChildren(make("span", "peek-label", labelParts(item.label).title));
    const hero = heroCard(item);
    if (hero) {
      peek.append(make("span", "peek-name", hero.name));
      if (item.cards.length > 1) {
        peek.append(make("span", "peek-more", `+${item.cards.length - 1}`));
      }
    } else {
      peek.append(make("span", "peek-more",
                       item.cards.length ? coverageWord(item.coverage) || "No key declaration" : "No declaration linked"));
    }
    peek.hidden = false;
    const stageWidth = Number.parseFloat(ui["page-stage"].style.width) || 0;
    const width = peek.offsetWidth || 0;
    const height = peek.offsetHeight || 0;
    const left = Math.max(0, Math.min(box.x, stageWidth - width));
    const above = box.y - 6 - height;
    peek.style.left = `${left}px`;
    peek.style.top = `${above >= 0 ? above : box.y + box.height + 6}px`;
  }

  function scrollRegionIntoView(region) {
    const box = state.boxes.get(region.id);
    const scroller = ui["page-scroller"];
    if (!box || typeof scroller.getBoundingClientRect !== "function") {
      return;
    }
    const stage = ui["page-stage"].getBoundingClientRect();
    const frame = scroller.getBoundingClientRect();
    const top = stage.top - frame.top + scroller.scrollTop + box.y;
    const bottom = top + box.height;
    const visibleBottom = scroller.scrollTop + scroller.clientHeight - CONTROLS_ROOM;
    if (top >= scroller.scrollTop && bottom <= visibleBottom) {
      return;
    }
    const reduce = windowRef?.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    scroller.scrollTo?.({
      behavior: reduce ? "auto" : "smooth",
      top: Math.max(0, (top + bottom) / 2 - (scroller.clientHeight - CONTROLS_ROOM) / 2),
    });
  }

  /** Each region's stroke: coverage always, and its verdict when verdicts are shown. */
  function decorateRegions() {
    for (const region of pageRegions()) {
      const verdict = state.faithfulness ? regionVerdict(regionItems(region)) : null;
      for (const polygon of polygonsOf(region.id)) {
        polygon.dataset.coverage = coverageOfRegion(region);
        if (verdict) {
          polygon.dataset.verdict = verdict;
        } else {
          delete polygon.dataset.verdict;
        }
      }
    }
  }

  function renderOverlay(displayed, viewport) {
    ui.overlay.replaceChildren();
    ui.overlay.setAttribute("viewBox", `0 0 ${viewport.width} ${viewport.height}`);
    ui.overlay.setAttribute("width", String(viewport.width));
    ui.overlay.setAttribute("height", String(viewport.height));
    state.boxes = new Map();
    for (const region of pageRegions()) {
      let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
      for (const quad of region.quads) {
        const points = quadToViewport(quad, displayed, viewport);
        for (const [x, y] of points) {
          [x0, y0, x1, y1] = [Math.min(x0, x), Math.min(y0, y), Math.max(x1, x), Math.max(y1, y)];
        }
        const polygon = documentRef.createElementNS(SVG_NS, "polygon");
        polygon.classList.add("overlay-region");
        polygon.dataset.regionId = region.id;
        polygon.setAttribute("points", points.map(([x, y]) => `${x},${y}`).join(" "));
        polygon.setAttribute("tabindex", "0");
        polygon.setAttribute("role", "button");
        polygon.setAttribute("aria-label", region.label);
        polygon.setAttribute("aria-pressed", String(state.region?.id === region.id));
        polygon.addEventListener("click", () => selectRegion(region));
        polygon.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            selectRegion(region);
          }
        });
        polygon.addEventListener("pointerenter", (event) => {
          if (event.pointerType !== "touch") {
            showPeek(region);
            setHot(region.id);
          }
        });
        polygon.addEventListener("pointerleave", () => {
          hidePeek();
          setHot(null);
        });
        polygon.addEventListener("focus", () => {
          if (polygon.matches?.(":focus-visible")) {
            showPeek(region);
            setHot(region.id);
          }
        });
        polygon.addEventListener("blur", () => {
          hidePeek();
          setHot(null);
        });
        ui.overlay.append(polygon);
      }
      state.boxes.set(region.id, { height: y1 - y0, width: x1 - x0, x: x0, y: y0 });
    }
    setClass(ui.overlay, "has-selection", state.region);
    decorateRegions();
    updateBar();
  }

  async function renderPage() {
    if (!state.pdfDocument) {
      return;
    }
    const epoch = ++state.renderEpoch;
    state.renderTask?.cancel();
    const page = await state.pdfDocument.getPage(state.currentPage);
    // Records place regions on the displayed page: after the page's own
    // rotation, which the reader's rotation adds to.
    const own = Number.isInteger(page.rotate) ? page.rotate : 0;
    const displayed = page.getViewport({ rotation: own, scale: 1 });
    const viewport = page.getViewport({ rotation: (own + state.rotation) % 360, scale: state.zoom / 100 });
    const deviceScale = Math.max(1, globalThis.devicePixelRatio || 1);
    const canvas = ui["pdf-canvas"];
    canvas.width = Math.ceil(viewport.width * deviceScale);
    canvas.height = Math.ceil(viewport.height * deviceScale);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    ui["page-stage"].style.width = `${viewport.width}px`;
    ui["page-stage"].style.height = `${viewport.height}px`;
    const context = canvas.getContext("2d", { alpha: false });
    state.renderTask = page.render({
      canvasContext: context,
      transform: deviceScale === 1 ? null : [deviceScale, 0, 0, deviceScale, 0, 0],
      viewport,
    });
    try {
      await state.renderTask.promise;
    } catch (error) {
      if (error?.name === "RenderingCancelledException") {
        return;
      }
      throw error;
    }
    if (epoch !== state.renderEpoch) {
      return;
    }
    hidePeek();
    renderOverlay(displayed, viewport);
    ui["page-number"].value = String(state.currentPage);
    ui["page-count"].textContent = `/ ${state.pdfDocument.numPages}`;
    ui["previous-page"].disabled = state.currentPage <= 1;
    ui["next-page"].disabled = state.currentPage >= state.pdfDocument.numPages;
    ui["zoom-value"].textContent = `${state.zoom}%`;
    setStatus("ok", "Verified PDF");
    rememberPage(state.config.storageKey, state.currentPage, { historyRef, storage });
  }

  /** Verdicts are hidden until asked for; the choice is remembered beside the page. */
  function showFaithfulness(shown) {
    state.faithfulness = shown;
    ui["toggle-faithfulness"].setAttribute("aria-pressed", String(shown));
    ui["toggle-faithfulness"].textContent = shown ? "Hide faithfulness" : "Show faithfulness";
    try {
      storage?.setItem(`${state.config.storageKey}-faithfulness`, shown ? "1" : "0");
    } catch {
      // Storage may be blocked (private mode); the button still works for this visit.
    }
    decorateRegions();
    if (state.selected) {
      renderItem(state.selected);
    } else if (state.index) {
      renderIndex();
    }
  }

  async function changePage(requested) {
    if (!state.pdfDocument) {
      return;
    }
    state.currentPage = Math.min(Math.max(Number(requested) || 1, 1), state.pdfDocument.numPages);
    selectRegion(null);
    await renderPage();
  }

  async function changeZoom(requested) {
    state.zoom = Math.min(Math.max(Number(requested) || 100, 50), 250);
    await renderPage();
  }

  async function rotate(delta) {
    state.rotation = (state.rotation + delta + 360) % 360;
    await renderPage();
  }

  // A refusal is an error message on the page, never a blank page: the two data
  // files are loaded inside this guard so the shell always says what went wrong.
  try {
    setStatus("loading", "Loading…");
    state.config = await loadConfig(fetchImpl, configUrl);
    applyConfig(state.config);
    state.index = (await loadAnnotations(state.config, fetchImpl, cryptoProvider, pageUrl, generatedUrl)).index;
  } catch (error) {
    ui.workspace.hidden = true;
    setStatus("bad", "Data not loaded");
    showError(error);
    throw error;
  }
  // A region the document places off the page, or in pt on a page whose size it
  // never declares, is dropped rather than guessed at; the page says how many.
  setNotice(state.index.warnings.length
    ? `${state.index.warnings.length} region(s) could not be placed and are not shown. `
      + state.index.warnings[0]
    : "");
  ui["toggle-faithfulness"].hidden = ![...state.index.items.values()].some(
    (item) => item.verdicts.length || item.cards.some((card) => card.verdict),
  );
  try {
    showFaithfulness(storage?.getItem(`${state.config.storageKey}-faithfulness`) === "1");
  } catch {
    // Unreadable storage leaves verdicts hidden.
  }
  clearError();
  try {
    const { bytes } = await loadPdfBytes(state.config, state.index, fetchImpl, cryptoProvider);
    const pdfjs = await pdfjsLoader();
    pdfjs.GlobalWorkerOptions.workerSrc = new URL(
      "../vendor/pdfjs/pdf.worker.min.mjs",
      import.meta.url,
    ).href;
    state.pdfDocument = await pdfjs.getDocument({
      data: bytes.slice(),
      isEvalSupported: false,
    }).promise;
    const remembered = rememberedPage(state.config.storageKey, { locationRef, storage });
    state.currentPage = remembered
      ? Math.min(Math.max(remembered, 1), state.pdfDocument.numPages)
      : 1;
    state.rotation = 0;
    state.zoom = 100;
    ui.workspace.hidden = false;
    selectRegion(null);
    await renderPage();
  } catch (error) {
    ui.workspace.hidden = true;
    setStatus("bad", "PDF not verified");
    showError(error);
  }

  ui["previous-page"].addEventListener("click", () => changePage(state.currentPage - 1));
  ui["next-page"].addEventListener("click", () => changePage(state.currentPage + 1));
  ui["page-number"].addEventListener("change", () => changePage(ui["page-number"].value));
  ui["zoom-out"].addEventListener("click", () => changeZoom(state.zoom - 10));
  ui["zoom-in"].addEventListener("click", () => changeZoom(state.zoom + 10));
  ui["rotate-right"].addEventListener("click", () => rotate(90));
  ui["toggle-faithfulness"].addEventListener("click", () => showFaithfulness(!state.faithfulness));
  ui["back-to-page"].addEventListener("click", () => selectRegion(null));
  ui["previous-item"].addEventListener("click", () => stepItem(-1));
  ui["next-item"].addEventListener("click", () => stepItem(1));
  ui["also-more"].addEventListener("click", () => {
    state.alsoAll = true;
    renderCards();
  });
  ui["target-filter"].addEventListener("input", () => {
    state.cardPage = 1;
    renderCards();
  });
  ui["previous-targets"].addEventListener("click", () => {
    state.cardPage -= 1;
    renderCards();
  });
  ui["next-targets"].addEventListener("click", () => {
    state.cardPage += 1;
    renderCards();
  });
  for (const lit of [ui["item-head"], ui.hero]) {
    lit.addEventListener("mouseenter", () => setHot(state.region?.id ?? null));
    lit.addEventListener("mouseleave", () => setHot(null));
  }
  // The status chip's note closes on a click anywhere else.
  documentRef.addEventListener("click", (event) => {
    if (ui["status-chip"].open && !ui["status-chip"].contains?.(event.target)) {
      ui["status-chip"].open = false;
    }
  });
  documentRef.addEventListener("keydown", (event) => {
    if (!isViewerShortcut(event)) {
      return;
    }
    if (event.key === "ArrowLeft") changePage(state.currentPage - 1);
    else if (event.key === "ArrowRight") changePage(state.currentPage + 1);
    else if (event.key === "+" || event.key === "=") changeZoom(state.zoom + 10);
    else if (event.key === "-") changeZoom(state.zoom - 10);
    else if (event.key.toLowerCase() === "r") rotate(90);
    else if (event.key === "Escape") selectRegion(null);
  });
  // An edited `#page=` in the address bar; the viewer's own replaceState fires no hashchange.
  windowRef?.addEventListener?.("hashchange", () => {
    const page = rememberedPage(null, { locationRef });
    if (page && page !== state.currentPage) changePage(page);
  });
  return { state, ui };
}


if (typeof document !== "undefined") {
  const start = () =>
    bootstrapViewer().catch((error) => {
      const errorElement = document.getElementById("error");
      const statusElement = document.getElementById("status");
      if (errorElement) {
        errorElement.textContent = error instanceof Error ? error.message : String(error);
        errorElement.hidden = false;
      }
      if (statusElement) statusElement.textContent = "Viewer initialization failed.";
    });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}
