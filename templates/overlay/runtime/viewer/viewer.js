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
 * `href` is used only when a card's page resolves to an `https:` URL or a
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
  firstKeyCard,
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
  sharedRole,
  splitCards,
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
 * site root: Literature Links resolves a relative reference against the file
 * that holds the annotation, and a card's rendered page is under the site
 * root. A bundle that does not match its recorded sha256
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
  return { files, index: buildIndex(files, config, { pageOrigin: new URL(pageUrl).origin, siteUrl: base }) };
}

/**
 * Fetch the PDF from the configured loopback route and verify its bytes against
 * the sha256 its subject ends state before anything renders them.
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

/**
 * A keydown the viewer acts on: no Ctrl, Alt or Command held, focus outside an
 * input, and Shift only where it types the key itself (`+` on many layouts),
 * never with a named key such as ArrowRight.
 */
export function isViewerShortcut(event) {
  if (!event || event.ctrlKey || event.metaKey || event.altKey) {
    return false;
  }
  if (event.shiftKey && String(event.key ?? "").length !== 1) {
    return false;
  }
  return !["INPUT", "SELECT", "TEXTAREA"].includes(event.target?.tagName);
}


/** Statements longer than this many lines are clamped, with a "Show all" button. */
const STATEMENT_LINES = 14;
/** About how many characters of a statement fit on one line of the panel before it wraps. */
const STATEMENT_COLUMNS = 70;
/** A line's closing " :" and the word before it, kept on one line so the colon never wraps alone. */
const CLOSING_COLON = /(?<!\S)\S{1,16} :(?=\n|$)/g;
/** The key declarations show this many cards before "Show N more". */
const KEY_CARDS = 8;
/** Above this many cards, the item view offers a filter by name. */
const FILTER_ABOVE = 20;
/** Room the floating PDF controls take at the bottom of the PDF pane. */
const CONTROLS_ROOM = 64;

const SVG_NS = "http://www.w3.org/2000/svg";

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
      "back-link", "page-heading", "page-lede", "keys",
      "error", "status", "status-glyph", "status-chip", "notice", "workspace",
      "previous-page", "next-page", "page-number", "page-count",
      "zoom-out", "zoom-in", "zoom-value", "rotate-right", "toggle-faithfulness",
      "page-scroller", "page-stage", "pdf-canvas", "overlay", "region-bar", "region-peek",
      "details", "selection-empty", "index-heading", "index-meta", "page-jump", "page-index", "legend",
      "verdict-legend", "index-footer",
      "selection", "back-to-page", "previous-item", "next-item", "item-position", "item-choices",
      "item-head", "item-label", "item-locator", "target-summary", "coverage-note", "verdict-notes",
      "key-section", "key-head", "key-count", "key-note", "key-list", "key-more",
      "related-section", "related-heading", "related-count", "related-list", "filter-empty", "target-filter",
      "related-pager", "previous-targets", "next-targets", "target-page", "provenance-note",
    ].map((id) => [id, element(documentRef, id)]),
  );
  const state = {
    boxes: new Map(),
    cardPage: 1,
    config: null,
    currentPage: 1,
    faithfulness: false,
    index: null,
    keysAll: false,
    pdfDocument: null,
    region: null,
    renderEpoch: 0,
    renderTask: null,
    rotation: 0,
    selected: null,
    zoom: 100,
  };

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
    // A minimal overlay keeps a verified PDF's chip for screen readers only;
    // loading and failure stay in sight.
    setClass(ui["status-chip"], "visually-hidden", Boolean(state.config?.minimal) && kind === "ok");
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
    // `display.minimal`: the toolbar buttons name their own shortcuts.
    ui.keys.hidden = config.minimal;
    // With no back link the top bar starts with the title; the stylesheet drops the divider too.
    const back = config.backLink;
    ui["back-link"].hidden = !back;
    if (back) {
      ui["back-link"].textContent = back.text;
      ui["back-link"].setAttribute("href", back.href);
      // The stylesheet draws a "←" before the link, unless its text already starts with one.
      setClass(ui["back-link"], "has-arrow", /^\s*[←‹<]/u.test(back.text));
    }
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

  /**
   * A declaration name: its namespace faint (fainter still when it repeats the
   * row above), its leaf strong. With `breaks`, a long name may wrap after a
   * namespace dot or a leaf underscore; one-line places (the page index) pass false.
   */
  function nameNode(name, className, previous = null, { breaks = true } = {}) {
    const node = make("span", className);
    const [namespace, leaf] = splitName(name);
    const pieces = (part, value, cut) => {
      for (const piece of breaks ? value.split(cut) : [value]) {
        part.append(piece);
        if (breaks) {
          part.append(make("wbr"));
        }
      }
      return part;
    };
    if (namespace) {
      node.append(pieces(make("span", namespace === previous ? "ns ditto" : "ns"), namespace, /(?<=\.)/));
    }
    node.append(pieces(make("span", "leaf"), leaf, /(?<=_)(?=.)/));
    return node;
  }

  /**
   * A Lean statement, lightly tinted; the text is the snippet's, exactly. Each
   * source line is its own block, so a line too long for the panel wraps under
   * its own indentation (a hanging indent), not back at the left edge.
   */
  function leanNode(source) {
    const node = make("pre", "lean");
    let line = null;
    let indent = 0;
    let leading = false;
    const open = () => {
      line = make("span", "ln");
      node.append(line);
      indent = 0;
      leading = true;
    };
    // Each line keeps its "\n", so the statement's text is the snippet's, exactly.
    const close = () => line?.style.setProperty?.("--ind", `${indent + 4}ch`);
    // Where each closing-colon span starts and ends, in turn; `nb` is the one open.
    const cuts = [...source.matchAll(CLOSING_COLON)].flatMap((match) => [match.index, match.index + match[0].length]);
    let nb = null;
    let at = 0;
    let broken = true;
    for (const [kind, value] of leanTokens(source)) {
      for (const part of value.split(/(?<=\n)/)) {
        if (broken) {
          close();
          open();
        }
        if (leading) {
          const spaces = /^ */.exec(part)[0].length;
          indent += spaces;
          leading = spaces === part.length;
        }
        for (let rest = part; rest;) {
          for (; cuts.length && cuts[0] <= at; cuts.shift()) {
            nb = nb ? null : make("span", "nb");
            if (nb) {
              line.append(nb);
            }
          }
          const piece = cuts.length ? rest.slice(0, cuts[0] - at) : rest;
          (nb ?? line).append(kind === "tx" ? piece : make("span", `tk-${kind}`, piece));
          at += piece.length;
          rest = rest.slice(piece.length);
        }
        broken = part.endsWith("\n");
      }
    }
    close();
    return node;
  }

  /**
   * A statement block, clamped past STATEMENT_LINES lines with a button to
   * show the rest. A long line counts as the lines it wraps to.
   */
  function statementNodes(source) {
    const pre = leanNode(source);
    const lines = source.split("\n")
      .reduce((total, line) => total + Math.max(1, Math.ceil(line.length / STATEMENT_COLUMNS)), 0);
    if (lines <= STATEMENT_LINES) {
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
  function cardVerdict(card) {
    if (!state.faithfulness || !card.verdict) {
      return null;
    }
    const display = declarationVerdictDisplay(card.verdict.verdict, card.verdict.status);
    const mark = make("span", `card-verdict verdict-badge ${display.className}`, display.label);
    mark.title = card.verdict.note;
    return mark;
  }

  // --- the page index (nothing selected) --------------------------------------

  function legendEntry(className, label) {
    const entry = make("span", "legend-entry");
    entry.append(make("i", className), label);
    return entry;
  }

  /** On a page with no passages: buttons to the nearest pages that have some. */
  function renderPageJump(empty) {
    const pages = empty ? [...state.index.pages.keys()].sort((a, b) => a - b) : [];
    const before = pages.filter((page) => page < state.currentPage).at(-1);
    const after = pages.find((page) => page > state.currentPage);
    const jump = (page, text) => {
      const button = make("button", "text-button", text);
      button.type = "button";
      button.addEventListener("click", async () => {
        await changePage(page);
        focusQuietly(ui["index-heading"]);
      });
      return button;
    };
    const buttons = [];
    if (before !== undefined) {
      buttons.push(jump(before, after === undefined ? `‹ Previous passage: p. ${before}` : `‹ p. ${before}`));
    }
    if (after !== undefined) {
      buttons.push(jump(after, `Next passage: p. ${after} ›`));
    }
    ui["page-jump"].replaceChildren(...buttons);
    ui["page-jump"].hidden = !buttons.length;
  }

  function renderIndex() {
    const entries = pageEntries();
    const items = entries.map((entry) => entry.item);
    ui["index-heading"].textContent = `Page ${state.currentPage}`;
    ui["index-meta"].textContent = pageSummaryText(items);
    renderPageJump(!entries.length);
    ui["page-index"].setAttribute("aria-label", `Passages on page ${state.currentPage}`);
    ui["page-index"].replaceChildren();
    ui["page-index"].hidden = !entries.length;
    ui.legend.hidden = !entries.length;
    for (const { item, region } of entries) {
      const row = make("button", "index-row");
      row.type = "button";
      row.dataset.regionId = region.id;
      row.dataset.itemId = item.id;
      // The same mark as the region's outline and the legend: solid, dashed or dotted.
      const mark = make("span", `swatch mini ${regionCoverage(item)}`);
      mark.title = coverageWord(item.coverage) || "No key declaration";
      row.append(mark);
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
      const lead = firstKeyCard(item);
      const right = make("span", lead ? "index-decl" : "index-decl is-empty");
      if (lead) {
        right.append(nameNode(lead.name, "decl", null, { breaks: false }));
        if (item.cards.length > 1) {
          right.append(make("span", "more", ` +${item.cards.length - 1}`));
        }
      } else {
        right.textContent = item.cards.length ? coverageWord(item.coverage) || "No key declaration"
          : "No declaration linked";
      }
      row.append(right);
      row.addEventListener("click", () => {
        selectRegion(region, item.id);
        focusQuietly(ui["item-label"]);
      });
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

  /** Move focus without scrolling, when the control that had it is about to be hidden or disabled. */
  function focusQuietly(node) {
    node?.focus?.({ preventScroll: true });
  }

  /** Back to the page index, with focus on the row of the item that was open. */
  function backToPage() {
    const was = state.selected?.id;
    selectRegion(null);
    const row = [...(ui["page-index"].children ?? [])].map((li) => li.children?.[0])
      .find((button) => button?.dataset?.itemId === was);
    focusQuietly(row ?? ui["index-heading"]);
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
    // A minimal overlay lets the cards say "formalized"; a word that qualifies them stays.
    const word = state.config.minimal && item.coverage === "formalized" ? "" : coverageWord(item.coverage);
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

  /** A key card's eyebrow: kind, verdict and badges on the left, the source link on the right. */
  function eyebrow(card) {
    const row = make("div", "eyebrow");
    const left = make("span", "eyebrow-left");
    if (!state.config.minimal) {
      left.append(make("span", "kind", card.kind || "declaration"));
    }
    const verdict = cardVerdict(card);
    if (verdict) {
      left.append(verdict);
    }
    for (const badge of state.config.minimal ? [] : card.badges) {
      left.append(make("span", badge.startsWith("sorry") ? "tag is-sorry" : "tag", badge));
    }
    row.append(left, sourceLink(card));
    return row;
  }

  /** A key declaration: its eyebrow, its name, and its statement in full. */
  function keyCard(card) {
    const li = make("li", "key-card");
    li.tabIndex = -1;
    const name = nameNode(card.name, "decl-name");
    name.title = card.fullName;
    li.append(eyebrow(card), name);
    if (card.snippet?.text) {
      li.append(...statementNodes(card.snippet.text));
    }
    return li;
  }

  /** What the key list says when it has no card: no key one, or nothing linked at all. */
  function renderKeyNote(item, keys) {
    const note = ui["key-note"];
    note.replaceChildren();
    note.hidden = keys.length > 0;
    if (keys.length) {
      return;
    }
    if (item.cards.length) {
      note.append(make("p", "empty-line", "No key declaration states this passage."));
      return;
    }
    note.append(make("p", "empty-line is-strong", "No Lean declaration is linked to this passage."));
    if (item.clusterIds.length && !state.config.minimal) {
      const line = make("p", "cluster-line", "Ledger cluster ");
      line.append(make("code", "cluster-ids", item.clusterIds.join(", ")));
      note.append(line);
    }
  }

  /**
   * A related declaration on one line: a plain link to its source, with its
   * name and a tag (its role when the heading does not say it, its kind when
   * that is not the same word, and "no page" when it has nowhere to go).
   */
  function relatedRow(card, { previous, shared }) {
    const li = make("li", "related-row");
    const row = card.href ? make("a", "related-link") : make("span", "related-link is-unlinked");
    if (card.href) {
      row.setAttribute("href", card.href);
    }
    const fallback = card.href && card.resolution === "fallback";
    const where = fallback ? card.fallbackLabel || "module page" : card.href ? "" : "No rendered page";
    row.title = [card.fullName, ...card.badges, where].filter(Boolean).join("\n");
    const name = nameNode(card.name, "related-name", previous);
    const tag = make("span", "related-tag");
    if (state.faithfulness && card.verdict) {
      const display = declarationVerdictDisplay(card.verdict.verdict, card.verdict.status);
      const dot = make("span", `dot verdict-dot ${display.className}`);
      dot.title = card.verdict.note ? `${display.label}: ${card.verdict.note}` : display.label;
      tag.append(dot);
    }
    const role = card.role === shared ? "" : card.role;
    // A minimal overlay drops the role and kind words, never where the link goes.
    const words = [state.config.minimal ? "" : role, state.config.minimal || card.kind === role ? "" : card.kind,
                   fallback ? "module page" : "", card.href ? "" : "no page"];
    tag.append(words.filter(Boolean).join(" · "));
    if (!state.config.minimal && card.badges.some((badge) => badge.startsWith("sorry"))) {
      tag.append(make("span", "tag is-sorry", "sorry"));
    }
    row.append(name, tag);
    li.append(row);
    return li;
  }

  function renderCards() {
    const item = state.selected;
    if (!item) {
      return;
    }
    const filtered = item.cards.length > FILTER_ABOVE && !state.config.minimal;
    const query = filtered ? ui["target-filter"].value : "";
    const { keys, related } = splitCards(item);
    const shownKeys = cardsMatching(keys, { query });
    const shownRelated = cardsMatching(related, { query });

    renderKeyNote(item, keys);
    const capped = state.keysAll ? shownKeys : shownKeys.slice(0, KEY_CARDS);
    ui["key-list"].replaceChildren(...capped.map(keyCard));
    ui["key-list"].hidden = !capped.length;
    // A neutral count, only when there is more than one: no heading says what the cards state.
    ui["key-count"].hidden = keys.length < 2 || state.config.minimal;
    ui["key-count"].textContent = query ? `${shownKeys.length} of ${keys.length} key declarations`
      : `${keys.length} key declarations`;
    const hidden = shownKeys.length - capped.length;
    ui["key-more"].hidden = hidden <= 0;
    ui["key-more"].textContent = hidden > 0 ? `Show ${hidden} more` : "";

    ui["related-section"].hidden = !related.length && !filtered;
    // The role most related cards share is said once, in the heading; a row
    // says its role only when it is another one.
    const shared = sharedRole(related);
    ui["related-heading"].replaceChildren("Related");
    if (shared) {
      ui["related-heading"].append(make("span", "related-role", ` · ${shared}`));
    }
    ui["related-count"].textContent = String(query ? shownRelated.length : related.length);
    ui["related-count"].hidden = state.config.minimal;
    ui["target-filter"].hidden = !filtered;
    // The count and the filter share a line above the key cards; with neither, no line.
    ui["key-head"].hidden = ui["key-count"].hidden && !filtered;
    ui["filter-empty"].hidden = !query || shownKeys.length + shownRelated.length > 0;
    const page = paginateCards(shownRelated, state.cardPage, CARD_PAGE_SIZE);
    state.cardPage = page.page;
    let previous = null;
    ui["related-list"].replaceChildren(...page.items.map((card) => {
      const row = relatedRow(card, { previous, shared });
      previous = splitName(card.name)[0] || null;
      return row;
    }));
    ui["related-pager"].hidden = page.pageCount <= 1;
    const first = (page.page - 1) * CARD_PAGE_SIZE + 1;
    ui["target-page"].textContent = page.total
      ? `${first}–${first + page.items.length - 1} of ${page.total}` : "";
    ui["previous-targets"].disabled = page.page <= 1;
    ui["next-targets"].disabled = page.page >= page.pageCount;
  }

  function renderFooter(item) {
    // A minimal overlay leaves out how the links were made, but shown verdicts
    // keep their caveat: the verdict line itself never says "unreviewed".
    const footer = !state.config.minimal ? reviewFooter(item, { faithfulness: state.faithfulness })
      : state.faithfulness ? verdictFooter([item]) : "";
    ui["provenance-note"].textContent = footer;
    ui["provenance-note"].hidden = !footer;
  }

  function renderItem(item) {
    renderNavStrip(item);
    renderHead(item);
    renderVerdictNotes(item);
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
    setHot(null);
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
    state.keysAll = false;
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
    const lead = firstKeyCard(item);
    if (lead) {
      peek.append(make("span", "peek-name", lead.name));
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
        // The selected region is the passage being read: hovering it adds nothing to light up.
        polygon.addEventListener("pointerenter", (event) => {
          if (event.pointerType !== "touch" && state.region?.id !== region.id) {
            showPeek(region);
            setHot(region.id);
          }
        });
        polygon.addEventListener("pointerleave", () => {
          hidePeek();
          setHot(null);
        });
        polygon.addEventListener("focus", () => {
          if (polygon.matches?.(":focus-visible") && state.region?.id !== region.id) {
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
    // Render off screen, so the page on screen stays until the next one is ready:
    // no blank or black frame on a page turn, a zoom or a rotation.
    const off = documentRef.createElement("canvas");
    off.width = Math.ceil(viewport.width * deviceScale);
    off.height = Math.ceil(viewport.height * deviceScale);
    const context = off.getContext("2d", { alpha: false });
    if (context?.fillRect) {
      context.fillStyle = "#fff";
      context.fillRect(0, 0, off.width, off.height);
    }
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
    const canvas = ui["pdf-canvas"];
    canvas.width = off.width;
    canvas.height = off.height;
    canvas.getContext("2d", { alpha: false })?.drawImage?.(off, 0, 0);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    ui["page-stage"].style.width = `${viewport.width}px`;
    ui["page-stage"].style.height = `${viewport.height}px`;
    ui["page-stage"].hidden = false;
    hidePeek();
    renderOverlay(displayed, viewport);
    if (state.region) {
      // A zoom or a rotation keeps the selected passage in view.
      scrollRegionIntoView(state.region);
    }
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
    // The controls show the real page and count from the start, never "/ 0".
    ui["page-number"].value = String(state.currentPage);
    ui["page-number"].max = String(state.pdfDocument.numPages);
    ui["page-count"].textContent = `/ ${state.pdfDocument.numPages}`;
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
  ui["back-to-page"].addEventListener("click", backToPage);
  for (const [id, delta, other] of [["previous-item", -1, "next-item"], ["next-item", 1, "previous-item"]]) {
    ui[id].addEventListener("click", () => {
      stepItem(delta);
      if (ui[id].disabled) {
        focusQuietly(ui[other].disabled ? ui["item-label"] : ui[other]);
      }
    });
  }
  ui["key-more"].addEventListener("click", () => {
    // The button goes away, so focus moves to the first card it showed.
    const at = ui["key-list"].children?.length ?? 0;
    state.keysAll = true;
    renderCards();
    focusQuietly(ui["key-list"].children?.[at]);
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
  for (const lit of [ui["item-head"], ui["key-section"]]) {
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
    else if (event.key === "Escape") {
      if (state.selected && ui.details.contains?.(documentRef.activeElement)) backToPage();
      else selectRegion(null);
    }
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
