/**
 * The overlay viewer's DOM controller.
 *
 * It fetches `../overlay.config.json`, then `../GENERATED.json`, then the
 * annotation bundle it records (checked against its recorded sha256): every
 * Literature Links annotation file with its path, in one fetch. It takes
 * every per-companion value from them: the page
 * title and heading, the back link, the PDF route and its sha256, the pages,
 * the regions, the items, the cards, the verdicts. `../verso.json`, when the
 * site build wrote one, gives a key card's statement Verso's tokens and hovers;
 * without it, or for a card it has no statement for, the plain statement
 * stays. The pure half (the bundle, the records, geometry, href rules,
 * hashing, the Verso runs) is in viewer-core.js, so `node --test` reaches it
 * without a DOM; this file keeps the injectable `fetch` / `document` /
 * `storage` seams the tests drive it through.
 *
 * Each rendered page carries PDF.js's own text layer (`startTextLayer`) under
 * the region boxes, so the page's text can be selected and copied; the spans
 * are the vendored build's, and viewer.css has the rules they are written for.
 *
 * Security (docs/overlay.md section 4): every string from those files reaches
 * the DOM through `textContent` or an attribute setter, never `innerHTML`. Explicit
 * LaTeX labels use locally bundled KaTeX to generate MathML (viewer-math.js); an
 * `href` is used only when a card's page resolves to an `https:` URL or a
 * page of this site, and a token's only when it is a page of this site; the
 * hover card and the text layer's scale are placed through CSSOM, never a
 * style attribute; the PDF is requested only from `pdf.route`, or, when
 * `pdf.publish` names an open license, from `../<file_name>` beside the
 * config; PDF.js runs with `isEvalSupported: false`.
 */
import { mathRuns, mathNode } from "./viewer-math.js";
import { installPanelResize } from "./viewer-layout.js";
import {
  CARD_PAGE_SIZE,
  CONFIG_URL,
  GENERATED_URL,
  MAX_JSON_BYTES,
  VERDICT_LABELS,
  VERSO_URL,
  buildIndex,
  cardsMatching,
  coverageSummary,
  coverageWord,
  declarationVerdictDisplay,
  docBlocks,
  firstKeyCard,
  hoverText,
  isStep,
  itemFolder,
  labelParts,
  leanTokens,
  linesText,
  locatorTail,
  pageRows,
  pageSummaryText,
  paginateCards,
  parseBundle,
  pdfFetchUrl,
  preferredItemId,
  quadToViewport,
  readConfig,
  readAnnotationsBlock,
  readVerso,
  regionCoverage,
  regionVerdict,
  reviewFooter,
  scriptRuns,
  sha256Hex,
  sharedRole,
  siteHref,
  splitCards,
  splitName,
  stepCards,
  stepParts,
  stepRelation,
  verdictFooter,
  verdictLine,
  verifyPdfBytes,
  versoRuns,
} from "./viewer-core.js";
import { createPdfTextIndex, searchTextRange } from "./viewer-search.js";

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
    .map(({ annotation, path }) => ({ folder: itemFolder(path, block.directory), path, records: [annotation],
                                      url: new URL(path, base).href }));
  return { files, index: buildIndex(files, config, { pageOrigin: new URL(pageUrl).origin, siteUrl: base }) };
}

/**
 * Read `verso.json`, or null: a site built without one, or a file that does
 * not read, leaves every card with its plain statement and no hovers.
 */
export async function loadVerso(fetchImpl = globalThis.fetch, url = VERSO_URL) {
  try {
    return readVerso(parseJsonBytes(await checkedFetch(url, fetchImpl), "verso.json"));
  } catch {
    return null;
  }
}

/**
 * Fetch the PDF from the configured loopback route, or from beside the config
 * when it is published, and verify its bytes against the sha256 its subject
 * ends state before anything renders them.
 */
export async function loadPdfBytes(config, index, fetchImpl = globalThis.fetch,
                                   cryptoProvider = globalThis.crypto, pageUrl = globalThis.location?.href) {
  if (typeof fetchImpl !== "function") {
    throw new Error("Fetch is unavailable; the verified PDF cannot be loaded.");
  }
  // An error names the PDF as the reader knows it: on loopback, or published with the site.
  const what = config.pdfLicense ? "published PDF" : "loopback PDF";
  const response = await fetchImpl(pdfFetchUrl(config, pageUrl), {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response?.ok) {
    throw new Error(
      `Unable to load the verified ${what} (${response?.status ?? "no response"}).`,
    );
  }
  const mediaType = response.headers?.get?.("Content-Type")?.split(";", 1)[0].trim().toLowerCase();
  if (mediaType !== PDF_MEDIA_TYPE) {
    throw new Error(`The ${what} response has unexpected media type ${mediaType || "missing"}.`);
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  const declared = index.bytes;
  if (Number.isInteger(declared) && bytes.byteLength !== declared) {
    throw new Error(`The ${what} is ${bytes.byteLength} bytes; the source record says ${declared}.`);
  }
  const verification = await verifyPdfBytes(bytes, index.sha256, cryptoProvider);
  if (!verification.ok) {
    throw new Error(
      `${what[0].toUpperCase()}${what.slice(1)} SHA-256 is ${verification.actual}; expected ${verification.expected}.`,
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

/**
 * Start PDF.js's own text layer for `page` in `container`: the page's text as
 * transparent spans over the canvas, which the reader selects and copies. The
 * vendored build's `TextLayer` writes every span and sizes the container from
 * `--total-scale-factor`, which is the viewport's scale times the page's user
 * unit (viewer.css says what each value it sets is read by). The returned
 * layer renders on `render()` and stops on `cancel()`; with no `TextLayer` —
 * an older vendored build — the page renders without one and nothing else
 * changes.
 */
export function startTextLayer({ TextLayer, container, page, viewport }) {
  container.replaceChildren();
  container.removeAttribute?.("data-main-rotation");
  if (typeof TextLayer !== "function" || typeof page?.streamTextContent !== "function") {
    return null;
  }
  container.style?.setProperty?.("--total-scale-factor", String(viewport.scale * (viewport.userUnit || 1)));
  return new TextLayer({
    container,
    // The text as the PDF spells it: normalization is for searching, not for copying.
    textContentSource: page.streamTextContent({ disableNormalization: true }),
    viewport,
  });
}


/** Statements longer than this many lines are clamped to it, with a "Show all" button. */
const STATEMENT_LINES = 14;
/** Up to this many lines more are shown whole: a clamp that would hide only a few lines is not worth a click. */
const STATEMENT_SLACK = 6;
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
/** The gap between a token and its hover card, and between the card and the window's edge. */
const HOVER_GAP = 6;
const HOVER_MARGIN = 8;
/** How long a hover card stays once the pointer leaves its token or the card: time to reach the card. */
export const HOVER_LINGER = 300;
/** Keys that scroll an open hover card while its token has focus, by how much of the card. */
const HOVER_PAGES = Object.freeze({ PageDown: 1, PageUp: -1 });
/** Keys that move focus between a statement's tokens, by how far. */
const TOKEN_STEPS = Object.freeze({ ArrowLeft: -1, ArrowRight: 1 });

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
  renderMath = mathNode,
  pdfjsLoader = () => import("../vendor/pdfjs/pdf.min.mjs"),
  versoUrl = VERSO_URL,
  searchDelay = 150,
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
      "error", "status", "status-glyph", "status-chip", "status-note", "notice", "workspace", "panel-divider",
      "previous-page", "next-page", "page-number", "page-count",
      "find-toggle", "find-bar", "find-input", "find-count", "find-previous", "find-next", "find-close", "find-highlights",
      "zoom-out", "zoom-in", "zoom-value", "rotate-right", "toggle-faithfulness", "toggle-reading",
      "page-scroller", "page-stage", "pdf-canvas", "text-layer", "overlay", "region-bar", "region-peek",
      "details", "selection-empty", "index-heading", "index-meta", "page-jump", "page-index", "legend",
      "verdict-legend", "index-footer",
      "selection", "back-to-page", "previous-item", "next-item", "item-position", "item-choices",
      "item-head", "step-parent", "item-label", "item-locator", "target-summary", "note-toggle", "coverage-note",
      "verdict-notes",
      "key-section", "key-head", "key-count", "key-note", "key-list", "key-more",
      "step-section", "step-count", "step-list",
      "related-section", "related-heading", "related-count", "related-list", "filter-empty", "target-filter",
      "related-pager", "previous-targets", "next-targets", "target-page", "provenance-note", "hover-card",
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
    noteOpen: false,
    pageOrigin: "",
    pdfDocument: null,
    region: null,
    readingMode: false,
    renderEpoch: 0,
    renderTask: null,
    rotation: 0,
    selected: null,
    search: { epoch: 0, index: null, matches: [], active: -1, busy: false, timer: null, scroll: false, failedPages: [] },
    siteUrl: "",
    textLayer: null,
    TextLayer: null,
    verso: null,
    zoom: 100,
  };
  /**
   * The token whose hover card is open, if any; the pending close, when the
   * pointer has left; whether the pointer is on the card or a tap on it is
   * under way, either of which keeps it; and, while a token with a card has
   * keyboard focus, what opens that card again once the pointer has gone.
   */
  const hover = { focus: null, over: false, pressed: false, timer: null, token: null };
  const setTimer = typeof windowRef?.setTimeout === "function" ? windowRef.setTimeout.bind(windowRef)
    : globalThis.setTimeout;
  const clearTimer = typeof windowRef?.clearTimeout === "function" ? windowRef.clearTimeout.bind(windowRef)
    : globalThis.clearTimeout;

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

  /** Typeset explicit LaTeX; keep legacy extracted scripts and plain text readable. */
  function setTitleText(node, value, depth = 0) {
    if (depth === 0) {
      const parts = mathRuns(value);
      if (parts.some((part) => part.kind === "math")) {
        node.replaceChildren(...parts.map((part) => part.kind === "math"
          ? (renderMath(part.text, documentRef) ?? part.raw)
          : setTitleText(make("span", ""), part.text, 1)));
        return node;
      }
    }
    const runs = depth < 32 ? scriptRuns(value) : [["text", value]];
    if (runs.some(([kind]) => kind !== "text")) {
      node.replaceChildren(...runs.map(([kind, run]) => (kind === "text" ? run
        : setTitleText(make(kind, ""), run, depth + 1))));
    } else {
      node.textContent = value;
    }
    return node;
  }

  function makeTitle(tag, className, value) {
    return setTitleText(make(tag, className), value);
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
    // The shell says the PDF never leaves loopback; a published one did, under its license.
    if (config.pdfLicense) {
      ui["status-note"].textContent = `The PDF is published with this site under ${config.pdfLicense}. `
        + "This page checks its SHA-256 against the one the annotations pin before PDF.js renders it.";
    }
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
   * The page index's rows: every item on the page once, with the first region
   * that shows it and its steps on this page. A step's item may come from
   * another page, with no region.
   */
  function pageIndexRows() {
    return pageRows(pageRegions(), state.index?.items ?? new Map());
  }

  /** The rows with a region on this page: the order the stepper walks. */
  function pageEntries() {
    return pageIndexRows().filter((row) => row.region);
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
    for (const button of [...indexButtons(), ...[...(ui["step-list"].children ?? [])].map((li) => li.children?.[0])]) {
      setClass(button, "is-hot", regionId && button?.dataset?.regionId === regionId);
    }
  }

  /** The page index's buttons: each item's row, then its steps'. */
  function indexButtons() {
    return [...(ui["page-index"].children ?? [])].flatMap((li) => {
      const [row, steps] = li.children ?? [];
      return [row, ...[...(steps?.children ?? [])].map((step) => step.children?.[0])];
    });
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
   * its own indentation (a hanging indent), not back at the left edge. With
   * Verso `runs` (their texts joined are the snippet) a token carries its
   * kind, its hover, its binding and its link. With `firstLine`, each line
   * carries its number for the stylesheet's gutter, outside the text.
   */
  function leanNode(source, runs = null, statement = { bindings: new Map(), reveal: null, stops: [] }, firstLine = null) {
    const node = make("pre", firstLine === null ? "lean" : "lean is-numbered");
    let line = null;
    let indent = 0;
    let leading = false;
    let number = firstLine;
    const open = () => {
      line = make("span", "ln");
      if (number !== null) {
        line.dataset.line = String(number);
        number += 1;
      }
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
    for (const run of runs ?? leanTokens(source)) {
      const [kind, value] = run;
      const plain = !run[2] && !run[3] && !run[4];
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
          (nb ?? line).append(!plain ? tokenNode(run, piece, statement)
            : kind === "tx" ? piece : make("span", `tk-${kind}`, piece));
          at += piece.length;
          rest = rest.slice(piece.length);
        }
        broken = part.endsWith("\n");
      }
    }
    close();
    // One tab stop per statement: the arrow keys move between its tokens.
    statement.stops.forEach((stop, index) => {
      stop.tabIndex = index ? -1 : 0;
    });
    return node;
  }

  /** One Verso token: tinted by kind, a link when its target is on the site, with a hover when it has one. */
  function tokenNode(run, piece, statement) {
    const [kind, , , binding, href] = run;
    const target = href ? siteHref(href, state.siteUrl, state.pageOrigin) : null;
    const node = make(target ? "a" : "span", kind === "tx" ? "tok" : `tk-${kind}`, piece);
    if (target) {
      node.setAttribute("href", target);
    }
    if (binding) {
      node.dataset.binding = String(binding);
      statement.bindings.set(binding, [...(statement.bindings.get(binding) ?? []), node]);
    }
    const shown = hoverText(state.verso, run);
    setClass(node, "has-hover", shown);
    const bind = (on) => {
      for (const other of binding ? statement.bindings.get(binding) : []) {
        setClass(other, "is-bound", on);
      }
    };
    const enter = () => {
      bind(true);
      if (shown) {
        showHover(node, shown);
      }
    };
    const leave = () => {
      bind(false);
      if (hover.token === node) {
        hideHoverSoon();
      }
    };
    node.addEventListener("pointerenter", (event) => event.pointerType !== "touch" && enter());
    node.addEventListener("pointerleave", (event) => event.pointerType !== "touch" && leave());
    if (!shown && !target) {
      return node;
    }
    statement.stops.push(node);
    node.addEventListener("focus", () => {
      for (const stop of statement.stops) {
        stop.tabIndex = stop === node ? 0 : -1;
      }
      statement.reveal?.(node);
      // Only keyboard focus holds the card; a click's focus leaves it to the pointer.
      hover.focus = shown && node.matches?.(":focus-visible") ? enter : null;
      enter();
    });
    // Focus leaving the token closes its card, unless that is a click or a tap into the card.
    node.addEventListener("blur", () => {
      bind(false);
      hover.focus = null;
      if (hover.token === node && !hover.over && !hover.pressed) {
        hideHover();
      }
    });
    // A first tap opens the hover; a second follows the link.
    let tapped = null;
    node.addEventListener("pointerdown", (event) => {
      tapped = event.pointerType === "touch" ? hover.token === node : null;
    });
    node.addEventListener("click", (event) => {
      if (tapped === false && shown) {
        event.preventDefault();
        enter();
      }
      tapped = null;
    });
    node.addEventListener("keydown", (event) => {
      // Alt+ArrowLeft, Command+ArrowLeft and the like are the browser's.
      if (!isViewerShortcut(event)) {
        return;
      }
      const stops = statement.stops;
      const at = stops.indexOf(node);
      const next = event.key === "Home" ? 0 : event.key === "End" ? stops.length - 1
        : event.key in TOKEN_STEPS ? Math.min(Math.max(at + TOKEN_STEPS[event.key], 0), stops.length - 1) : null;
      if (event.key in HOVER_PAGES && hover.token === node && scrollHover(HOVER_PAGES[event.key])) {
        event.preventDefault();
        event.stopPropagation?.();
      } else if (next !== null) {
        // The arrows turn the PDF's pages everywhere else.
        event.preventDefault();
        event.stopPropagation?.();
        stops[next].focus?.();
      }
    });
    return node;
  }

  /** The hover card's content: the signature, then the docstring's paragraphs and code, all as text. */
  function hoverNodes({ doc, signature }) {
    const nodes = signature ? [make("code", "hover-sig", signature)] : [];
    for (const block of docBlocks(doc)) {
      const node = make(block.code ? "pre" : "p", block.code ? "hover-code" : "hover-doc");
      for (const [kind, value] of block.parts) {
        node.append(kind === "code" && !block.code ? make("code", "", value) : value);
      }
      nodes.push(node);
    }
    return nodes;
  }

  /** Open the hover card by a token. */
  function showHover(node, shown) {
    const card = ui["hover-card"];
    const fresh = hover.token !== node;
    cancelHideHover();
    if (fresh) {
      hover.token?.removeAttribute?.("aria-describedby");
      card.replaceChildren(...hoverNodes(shown));
    }
    hover.token = node;
    node.setAttribute("aria-describedby", "hover-card");
    card.hidden = false;
    // Once shown: a hidden element keeps no scroll position of its own to set.
    if (fresh) {
      card.scrollTop = 0;
    }
    placeHover();
  }

  /** The open card under its token, or above it when there is no room below, inside the window. */
  function placeHover() {
    const card = ui["hover-card"];
    const at = hover.token?.getBoundingClientRect?.();
    if (!at) {
      return;
    }
    // Measured at the window's left edge, so its width is its own, not what its last place left room for.
    card.style.left = "0px";
    card.style.top = "0px";
    const size = card.getBoundingClientRect?.() ?? { height: 0, width: 0 };
    const width = windowRef?.innerWidth ?? size.width;
    const height = windowRef?.innerHeight ?? size.height;
    // A token scrolled out of its panel, or of the window, takes its card with it, until it is back.
    const panel = ui.details.contains?.(hover.token) ? ui.details.getBoundingClientRect?.() : null;
    card.hidden = at.bottom < Math.max(panel?.top ?? 0, 0) || at.top > Math.min(panel?.bottom ?? height, height);
    const below = at.bottom + HOVER_GAP;
    const above = at.top - HOVER_GAP - size.height;
    const top = below + size.height <= height - HOVER_MARGIN || above < HOVER_MARGIN ? below : above;
    const left = Math.max(HOVER_MARGIN, Math.min(at.left, width - size.width - HOVER_MARGIN));
    card.style.left = `${Math.round(left)}px`;
    card.style.top = `${Math.round(top)}px`;
  }

  function hideHover() {
    cancelHideHover();
    hover.token?.removeAttribute?.("aria-describedby");
    hover.token = null;
    hover.focus = null;
    hover.over = false;
    hover.pressed = false;
    ui["hover-card"].hidden = true;
  }

  /**
   * Close the card after HOVER_LINGER, unless the pointer reaches the card or
   * another token first; a token with keyboard focus then has its own card back.
   */
  function hideHoverSoon() {
    cancelHideHover();
    hover.timer = setTimer(() => {
      hover.timer = null;
      if (!hover.over) {
        (hover.focus ?? hideHover)();
      }
    }, HOVER_LINGER);
  }

  function cancelHideHover() {
    if (hover.timer !== null) {
      clearTimer(hover.timer);
      hover.timer = null;
    }
  }

  /** Scroll the open card by about a card's height; false when it has nothing more to show that way. */
  function scrollHover(direction) {
    const card = ui["hover-card"];
    const room = (card.scrollHeight ?? 0) - (card.clientHeight ?? 0);
    const top = card.scrollTop ?? 0;
    if (card.hidden || room <= 0 || (direction > 0 ? top >= room : top <= 0)) {
      return false;
    }
    const step = Math.max(1, (card.clientHeight ?? 0) - 24);
    card.scrollTop = Math.min(room, Math.max(0, top + direction * step));
    return true;
  }

  /**
   * A statement block, clamped to STATEMENT_LINES lines when it is longer
   * than that and STATEMENT_SLACK, with a button to show the rest. A long
   * line counts as the lines it wraps to.
   */
  function statementNodes(source, runs = null, firstLine = null) {
    const statement = { bindings: new Map(), reveal: null, stops: [] };
    const pre = leanNode(source, runs, statement, firstLine);
    const lines = source.split("\n")
      .reduce((total, line) => total + Math.max(1, Math.ceil(line.length / STATEMENT_COLUMNS)), 0);
    if (lines <= STATEMENT_LINES + STATEMENT_SLACK) {
      return [pre];
    }
    setClass(pre, "is-clamped", true);
    const button = make("button", "text-button show-all", "Show all");
    button.type = "button";
    button.setAttribute("aria-expanded", "false");
    let clamped = true;
    const toggle = () => {
      clamped = !clamped;
      setClass(pre, "is-clamped", clamped);
      button.setAttribute("aria-expanded", String(!clamped));
      button.textContent = clamped ? "Show all" : "Show less";
    };
    button.addEventListener("click", toggle);
    // A token the clamp hides opens the statement when it takes focus. Focus
    // first scrolls the clamped block itself (its overflow is hidden) to show
    // the token, so a scrolled block counts as hiding it too.
    statement.reveal = (token) => {
      if (clamped && (pre.scrollTop > 0
                      || token.getBoundingClientRect?.().bottom > pre.getBoundingClientRect?.().bottom)) {
        toggle();
        pre.scrollTop = 0;
        token.scrollIntoView?.({ block: "nearest" });
      }
    };
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

  /**
   * One step on one line, under its item: the region's mark, its number, its
   * text and its relation, and its page when that is not this one.
   */
  function stepRow(step) {
    const parts = stepParts(step, state.index.items);
    const relation = stepRelation(step, { minimal: state.config.minimal });
    const row = make("button", "step-row");
    row.type = "button";
    row.title = step.label;
    row.dataset.itemId = step.id;
    const here = regionOnPage(step.id);
    const regionId = here?.id ?? step.place.regionId;
    row.dataset.regionId = regionId;
    row.append(make("span", `swatch mini ${regionCoverage(step)}`), make("span", "step-number", String(parts.number)),
               makeTitle("span", "step-text", parts.text));
    if (relation.word) {
      const word = make("span", "step-relation", relation.word);
      word.title = relation.title;
      row.append(word);
    }
    if (!here) {
      row.append(make("span", "step-page", `p. ${step.place.page}`));
    }
    row.addEventListener("click", async () => {
      await openItem(step.id);
      focusQuietly(ui["item-label"]);
    });
    row.addEventListener("mouseenter", () => setHot(regionId));
    row.addEventListener("mouseleave", () => setHot(null));
    row.addEventListener("focus", () => setHot(regionId));
    row.addEventListener("blur", () => setHot(null));
    const li = make("li");
    li.append(row);
    return li;
  }

  function renderIndex() {
    const rows = pageIndexRows();
    const entries = rows.filter((entry) => entry.region);
    const items = entries.map((entry) => entry.item);
    const stepCount = rows.reduce((total, entry) => total + entry.steps.length, 0);
    ui["index-heading"].textContent = `Page ${state.currentPage}`;
    ui["index-meta"].textContent = pageSummaryText(items, stepCount);
    renderPageJump(!rows.length);
    ui["page-index"].setAttribute("aria-label", `Passages on page ${state.currentPage}`);
    ui["page-index"].replaceChildren();
    ui["page-index"].hidden = !rows.length;
    ui.legend.hidden = !rows.length;
    for (const { item, region, steps } of rows) {
      const row = make("button", "index-row");
      row.type = "button";
      if (region) {
        row.dataset.regionId = region.id;
      }
      row.dataset.itemId = item.id;
      // The same mark as the region's outline and the legend: solid, dashed or dotted.
      const mark = make("span", `swatch mini ${regionCoverage(item)}`);
      mark.title = coverageWord(item.coverage) || "No key declaration";
      row.append(mark);
      const cell = make("span", "index-label");
      const label = makeTitle("span", "index-title", labelParts(item.label).title);
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
      // A step tied to no item keeps a row of its own: its ranges are one card, and its relation is printed.
      const step = isStep(item);
      const count = step ? stepCards(item).length : item.cards.length;
      const relation = step ? stepRelation(item, { minimal: state.config.minimal }).word : "";
      if (lead) {
        right.append(nameNode(lead.name, "decl", null, { breaks: false }));
        if (count > 1) {
          right.append(make("span", "more", ` +${count - 1}`));
        }
      } else if (relation) {
        right.textContent = relation;
      } else {
        right.textContent = item.cards.length ? coverageWord(item.coverage) || "No key declaration"
          : "No declaration linked";
      }
      if (lead && relation) {
        right.append(make("span", "more", ` ${relation}`));
      }
      if (!region) {
        // Its statement is on another page; only its steps are on this one.
        right.append(make("span", "more", ` p. ${item.place.page}`));
      }
      row.append(right);
      row.addEventListener("click", async () => {
        await (region ? selectRegion(region, item.id) : openItem(item.id));
        focusQuietly(ui["item-label"]);
      });
      if (region) {
        row.addEventListener("mouseenter", () => setHot(region.id));
        row.addEventListener("mouseleave", () => setHot(null));
        row.addEventListener("focus", () => setHot(region.id));
        row.addEventListener("blur", () => setHot(null));
      }
      const li = make("li");
      li.append(row);
      if (steps.length) {
        const list = make("ol", "step-list");
        list.setAttribute("aria-label", `Proof steps of ${labelParts(item.label).title}`);
        list.append(...steps.map((entry) => stepRow(entry.item)));
        li.append(list);
      }
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
      const button = makeTitle("button", "item-choice", labelParts(item.label).title);
      button.type = "button";
      button.title = item.label;
      button.dataset.itemId = itemId;
      button.setAttribute("aria-pressed", String(state.selected?.id === itemId));
      button.addEventListener("click", () => selectItem(itemId));
      ui["item-choices"].append(button);
    }
  }

  /** What the stepper walks from `item`: the steps of its proof, else the items on the page. */
  function stepperIds(item) {
    return state.index.items.get(item.stepOf)?.steps ?? pageEntries().map((entry) => entry.item.id);
  }

  function renderNavStrip(item) {
    const ids = stepperIds(item);
    const at = ids.indexOf(item.id);
    ui["back-to-page"].textContent = `← Page ${state.currentPage}`;
    ui["item-position"].textContent = at >= 0 ? `${at + 1} of ${ids.length}` : "";
    ui["previous-item"].disabled = at <= 0;
    ui["next-item"].disabled = at < 0 || at >= ids.length - 1;
    const walked = item.stepOf ? "step" : "passage";
    ui["previous-item"].setAttribute("aria-label", `Previous ${walked}`);
    ui["next-item"].setAttribute("aria-label", `Next ${walked}`);
  }

  /** Move focus without scrolling, when the control that had it is about to be hidden or disabled. */
  function focusQuietly(node) {
    node?.focus?.({ preventScroll: true });
  }

  /** Back to the page index, with focus on the row of the item that was open. */
  function backToPage() {
    const was = state.selected?.id;
    selectRegion(null);
    const row = indexButtons().find((button) => button?.dataset?.itemId === was);
    focusQuietly(row ?? ui["index-heading"]);
  }

  function regionOnPage(itemId) {
    return pageRegions().find((region) => region.itemIds.includes(itemId)) ?? null;
  }

  /**
   * Open an item, on this page if it has a region here, else on the first page
   * that shows it, with no page index between the two.
   */
  async function openItem(itemId) {
    const here = regionOnPage(itemId);
    if (here) {
      selectRegion(here, itemId);
      return;
    }
    const place = state.index.items.get(itemId)?.place;
    const region = place ? state.index.pages.get(place.page).find((candidate) => candidate.id === place.regionId) : null;
    if (region) {
      state.currentPage = place.page;
      selectRegion(region, itemId);
      await renderPage();
    }
  }

  async function stepItem(delta) {
    const ids = stepperIds(state.selected ?? {});
    const at = ids.indexOf(state.selected?.id);
    const next = at >= 0 ? ids[at + delta] : undefined;
    if (next === undefined) {
      return;
    }
    if (state.selected.stepOf) {
      await openItem(next);
    } else {
      selectRegion(pageEntries().find((entry) => entry.item.id === next).region, next);
    }
  }

  /** A step's title and its text; its proof's item, a button above them. */
  function stepHead(item) {
    const parts = stepParts(item, state.index.items);
    const owner = state.index.items.get(item.stepOf);
    ui["step-parent"].hidden = !owner;
    setTitleText(ui["step-parent"], owner ? `Proof of ${labelParts(owner.label).title}` : "");
    ui["step-parent"].title = owner?.label ?? "";
    ui["item-label"].replaceChildren(makeTitle("span", "item-title", parts.title));
    if (parts.text) {
      ui["item-label"].append(makeTitle("span", "item-subtitle", parts.text));
    }
  }

  /** A note's text with its backticked code set as code. */
  function noteNodes(note) {
    return docBlocks(note).flatMap((block) => block.parts.map(([kind, value]) => (kind === "code"
      ? make("code", "", value) : value)));
  }

  /**
   * A minimal overlay keeps a record's note behind one control, closed until
   * asked for and then open from record to record; a full one shows it.
   */
  function renderNote(item) {
    // Older exporters sometimes copied the heading into the optional note.
    const normalize = value => (value ?? "").trim().replace(/\s+/gu, " ");
    const noteText = normalize(item?.coverageNote);
    const heading = isStep(item) ? stepParts(item, state.index.items).text : item?.label;
    const note = Boolean(noteText) && ![item?.label, heading].some(label => normalize(label) === noteText);
    const behind = note && state.config.minimal;
    ui["note-toggle"].hidden = !behind;
    ui["note-toggle"].textContent = state.noteOpen ? "Less detail" : "More detail";
    ui["note-toggle"].setAttribute("aria-expanded", String(state.noteOpen));
    ui["coverage-note"].hidden = !note || (behind && !state.noteOpen);
  }

  function renderHead(item) {
    const step = isStep(item);
    if (step) {
      stepHead(item);
    } else {
      const parts = labelParts(item.label);
      ui["step-parent"].hidden = true;
      ui["item-label"].replaceChildren(makeTitle("span", "item-title", parts.title));
      if (parts.subtitle) {
        ui["item-label"].append(makeTitle("span", "item-subtitle", parts.subtitle));
      }
    }
    // A minimal overlay lets the cards say "formalized"; a word that qualifies them stays.
    // A step says its relation instead, which its coverage only restates.
    const relation = stepRelation(item, { minimal: state.config.minimal });
    const word = step ? relation.word
      : state.config.minimal && item.coverage === "formalized" ? "" : coverageWord(item.coverage);
    ui["target-summary"].className = item.coverage
      ? `target-summary coverage-${item.coverage}` : "target-summary";
    ui["target-summary"].textContent = word;
    ui["target-summary"].title = step ? relation.title : coverageSummary(item.coverage);
    ui["target-summary"].hidden = !word;
    const tail = locatorTail(item.label, item.locator);
    setTitleText(ui["item-locator"], tail);
    ui["item-locator"].title = item.locator;
    ui["item-locator"].hidden = !tail;
    ui["coverage-note"].textContent = step ? "" : item.coverageNote;
    if (step) {
      ui["coverage-note"].replaceChildren(...noteNodes(item.coverageNote));
    }
    renderNote(item);
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
      li.append(...statementNodes(card.snippet.text, versoRuns(state.verso, card)));
    }
    return li;
  }

  /** One range of a step's Lean lines: its snippet, numbered, or only its line numbers when it has none. */
  function rangeNodes({ lines, snippet, target }) {
    const firstLine = snippet?.line ?? lines?.[0] ?? null;
    if (!snippet?.text) {
      return lines ? [make("p", "range-lines", linesText(lines))] : [];
    }
    const nodes = statementNodes(snippet.text, versoRuns(state.verso, { target, snippet }), firstLine);
    if (lines) {
      nodes[0].setAttribute("aria-label", linesText(lines));
    }
    if (snippet.truncated && lines) {
      nodes.splice(1, 0, make("p", "range-more", `… through line ${lines[1]}`));
    }
    return nodes;
  }

  /** A declaration a step links: its eyebrow, its name, and only the lines of it the step names. */
  function stepCard(card) {
    const li = make("li", "key-card");
    li.tabIndex = -1;
    const name = nameNode(card.name, "decl-name");
    name.title = card.fullName;
    li.append(eyebrow(card), name, ...card.ranges.flatMap(rangeNodes));
    return li;
  }

  /** A declaration or module a checked absence looked in, on one line. */
  function checkedRow(end) {
    const li = make("li", "related-row");
    const row = end.href ? make("a", "related-link") : make("span", "related-link is-unlinked");
    if (end.href) {
      row.setAttribute("href", end.href);
    }
    row.title = end.fullName || end.module;
    row.append(nameNode(end.name, "related-name"));
    if (end.lines) {
      row.append(make("span", "related-tag", linesText(end.lines).toLowerCase()));
    }
    li.append(row);
    return li;
  }

  /** What a step with no card says when it was checked: the absence, and where it was looked for. */
  function checkedNote(item) {
    const note = ui["key-note"];
    note.append(make("p", "empty-line is-strong", "Checked: no Lean line makes this step."));
    if (item.checked.length) {
      const list = make("ul", "related-list checked-list");
      list.setAttribute("aria-label", "Where it was checked");
      list.append(...item.checked.map(checkedRow));
      note.append(make("p", "checked-in", "Checked in"), list);
    }
  }

  /** What the key list says when it has no card: no key one, or nothing linked at all. */
  function renderKeyNote(item, keys) {
    const note = ui["key-note"];
    note.replaceChildren();
    note.hidden = keys.length > 0;
    if (keys.length) {
      return;
    }
    if (isStep(item) && item.relations.includes("noMatch")) {
      checkedNote(item);
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
    hideHover();
    const item = state.selected;
    if (!item) {
      return;
    }
    const filtered = item.cards.length > FILTER_ABOVE && !state.config.minimal;
    const query = filtered ? ui["target-filter"].value : "";
    const step = isStep(item);
    const { keys, related } = step ? { keys: stepCards(item), related: [] } : splitCards(item);
    const shownKeys = cardsMatching(keys, { query });
    const shownRelated = cardsMatching(related, { query });

    renderKeyNote(item, keys);
    const capped = state.keysAll ? shownKeys : shownKeys.slice(0, KEY_CARDS);
    ui["key-list"].replaceChildren(...capped.map(step ? stepCard : keyCard));
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

  /** An item's proof steps, each opening its step, wherever on the book's pages it is. */
  function renderSteps(item) {
    ui["step-section"].hidden = !item.steps.length;
    ui["step-count"].textContent = String(item.steps.length);
    ui["step-count"].hidden = state.config.minimal;
    ui["step-list"].replaceChildren(...item.steps.map((id) => stepRow(state.index.items.get(id))));
  }

  function renderItem(item) {
    renderNavStrip(item);
    renderHead(item);
    renderVerdictNotes(item);
    renderCards();
    renderSteps(item);
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
    hideHover();
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
    ui["region-bar"].hidden = state.readingMode || !box;
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
    if (state.readingMode || !box || state.region?.id === region.id) {
      hidePeek();
      return;
    }
    const item = state.index.items.get(preferredItemId(region, state.index.items));
    const peek = ui["region-peek"];
    const step = isStep(item) && item.stepOf ? stepParts(item, state.index.items) : null;
    peek.replaceChildren(makeTitle("span", "peek-label", step ? step.title : labelParts(item.label).title));
    const lead = firstKeyCard(item);
    // A step's ends may be many ranges of one declaration: "+N" counts the declarations.
    const count = isStep(item) ? stepCards(item).length : item.cards.length;
    const relation = isStep(item) ? stepRelation(item, { minimal: state.config.minimal }).word : "";
    if (lead) {
      peek.append(make("span", "peek-name", lead.name));
      if (count > 1) {
        peek.append(make("span", "peek-more", `+${count - 1}`));
      }
      if (relation) {
        peek.append(make("span", "peek-more", relation));
      }
    } else if (relation) {
      peek.append(make("span", "peek-more", relation));
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
      const steps = regionItems(region).every(isStep);
      for (const polygon of polygonsOf(region.id)) {
        polygon.dataset.coverage = coverageOfRegion(region);
        if (steps) {
          polygon.dataset.step = "true";
        }
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

  function showSearch() {
    if (!state.search.index || ui.workspace.hidden) return;
    ui["find-bar"].hidden = false;
    ui["find-toggle"].setAttribute("aria-expanded", "true");
    focusQuietly(ui["find-input"]);
    ui["find-input"].select?.();
    paintSearch();
  }

  function closeSearch() {
    ++state.search.epoch;
    clearTimer(state.search.timer);
    state.search.timer = null;
    state.search.busy = false;
    ui["find-bar"].hidden = true;
    ui["find-toggle"].setAttribute("aria-expanded", "false");
    ui["find-highlights"].replaceChildren();
    focusQuietly(ui["find-toggle"]);
  }

  function searchCount() {
    const search = state.search;
    let text = search.matches.length ? `${search.active + 1} of ${search.matches.length}` : "No matches";
    if (search.failedPages.length) {
      const n = search.failedPages.length;
      text += ` · ${n} ${n === 1 ? "page could" : "pages could"} not be searched`;
    }
    ui["find-count"].textContent = text;
    ui["find-previous"].disabled = ui["find-next"].disabled = !search.matches.length;
  }

  /** Range rectangles already include the PDF text layer's zoom and rotation. */
  function paintSearch() {
    ui["find-highlights"].replaceChildren();
    if (ui["find-bar"].hidden || !state.textLayer || !ui["page-stage"].getBoundingClientRect) return;
    const origin = ui["page-stage"].getBoundingClientRect();
    let current = null;
    for (const [i, match] of state.search.matches.entries()) {
      if (match.page !== state.currentPage) continue;
      const range = searchTextRange(documentRef, state.textLayer, match);
      for (const rect of range?.getClientRects() ?? []) {
        if (!rect.width || !rect.height) continue;
        const mark = make("div", `find-highlight${i === state.search.active ? " is-current" : ""}`);
        mark.style.left = `${rect.left - origin.left}px`;
        mark.style.top = `${rect.top - origin.top}px`;
        mark.style.width = `${rect.width}px`;
        mark.style.height = `${rect.height}px`;
        ui["find-highlights"].append(mark);
        if (i === state.search.active && !current) current = rect;
      }
    }
    if (current && state.search.scroll) {
      const scroller = ui["page-scroller"];
      const box = scroller.getBoundingClientRect();
      scroller.scrollTop += current.top - box.top - box.height / 2;
      if (current.left < box.left || current.right > box.right) {
        scroller.scrollLeft += current.left - box.left - box.width / 2;
      }
      state.search.scroll = false;
    }
  }

  async function showMatch(at) {
    const search = state.search;
    if (!search.matches.length || search.busy) return;
    search.active = (at + search.matches.length) % search.matches.length;
    search.scroll = true;
    searchCount();
    const match = search.matches[search.active];
    if (match.page !== state.currentPage) await changePage(match.page);
    else paintSearch();
  }

  async function runSearch() {
    const search = state.search;
    clearTimer(search.timer);
    search.timer = null;
    const epoch = ++search.epoch;
    const query = ui["find-input"].value.trim();
    search.matches = [];
    search.active = -1;
    search.failedPages = [];
    search.busy = Boolean(query);
    ui["find-highlights"].replaceChildren();
    ui["find-previous"].disabled = ui["find-next"].disabled = true;
    ui["find-count"].textContent = query ? "Searching…" : "";
    if (!query) return;
    const result = await search.index.search(query, {
      cancelled: () => epoch !== search.epoch,
      onProgress: (page) => { ui["find-count"].textContent = `Searching ${page}/${state.pdfDocument.numPages}…`; },
    });
    if (!result || epoch !== search.epoch) return;
    search.busy = false;
    search.matches = result.matches;
    search.failedPages = result.failedPages;
    if (result.matches.length) {
      const near = result.matches.findIndex((match) => match.page >= state.currentPage);
      await showMatch(near < 0 ? 0 : near);
    } else {
      searchCount();
      if (!result.textPages && !result.failedPages.length) ui["find-count"].textContent = "No searchable text";
    }
  }

  async function renderPage() {
    if (!state.pdfDocument) {
      return;
    }
    const epoch = ++state.renderEpoch;
    state.renderTask?.cancel();
    // A text layer still streaming is the page being left: its spans would land on this one.
    state.textLayer?.cancel();
    state.textLayer = null;
    ui["text-layer"].replaceChildren();
    ui["find-highlights"].replaceChildren();
    const page = await state.pdfDocument.getPage(state.currentPage);
    if (epoch !== state.renderEpoch) return;
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
    if (state.region && !state.readingMode) {
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
    // The text comes after the page it belongs to: it streams, and nothing on
    // the page waits for it. A layer that fails leaves a page that cannot be
    // selected, and a viewer that works.
    try {
      state.textLayer = startTextLayer({ TextLayer: state.TextLayer, container: ui["text-layer"], page, viewport });
      await state.textLayer?.render();
    } catch {
      // `cancel()` on the next render rejects this one; so does a page whose text cannot be read.
    }
    if (epoch === state.renderEpoch) {
      state.search.scroll = true;
      paintSearch();
    }
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

  let panelResize = null;

  /** Read the PDF alone, retaining the page, zoom and selected passage. */
  function showReadingMode(shown) {
    state.readingMode = shown;
    setClass(ui.workspace, "is-reading", shown);
    ui.details.hidden = shown;
    ui["panel-divider"].hidden = shown;
    panelResize?.refresh();
    // SVG has no HTMLElement.hidden setter: write the attribute for [hidden].
    if (shown) ui.overlay.setAttribute("hidden", "");
    else ui.overlay.removeAttribute("hidden");
    ui["toggle-reading"].textContent = shown ? "Show links" : "Read PDF";
    ui["toggle-reading"].setAttribute("aria-expanded", String(!shown));
    ui["toggle-reading"].title = shown
      ? "Show annotation boxes and the Lean panel (Esc)"
      : "Hide annotation boxes and the Lean panel";
    ui["page-scroller"].setAttribute("aria-label", shown
      ? "Rendered PDF page" : "Rendered PDF page with selectable source regions");
    hidePeek();
    hideHover();
    setHot(null);
    updateBar();
  }

  ui["toggle-reading"].disabled = true;
  ui["find-toggle"].disabled = true;
  ui["find-bar"].hidden = true;
  ui["find-previous"].disabled = ui["find-next"].disabled = true;
  // A refusal is an error message on the page, never a blank page: the two data
  // files are loaded inside this guard so the shell always says what went wrong.
  try {
    setStatus("loading", "Loading…");
    state.config = await loadConfig(fetchImpl, configUrl);
    applyConfig(state.config);
    state.index = (await loadAnnotations(state.config, fetchImpl, cryptoProvider, pageUrl, generatedUrl)).index;
    state.siteUrl = new URL(state.config.siteRoot, pageUrl).href;
    state.pageOrigin = new URL(pageUrl).origin;
    state.verso = await loadVerso(fetchImpl, versoUrl);
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
    const { bytes } = await loadPdfBytes(state.config, state.index, fetchImpl, cryptoProvider, pageUrl);
    const pdfjs = await pdfjsLoader();
    pdfjs.GlobalWorkerOptions.workerSrc = new URL(
      "../vendor/pdfjs/pdf.worker.min.mjs",
      import.meta.url,
    ).href;
    state.TextLayer = pdfjs.TextLayer ?? null;
    state.pdfDocument = await pdfjs.getDocument({
      data: bytes.slice(),
      isEvalSupported: false,
    }).promise;
    state.search.index = createPdfTextIndex(state.pdfDocument);
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
    panelResize = installPanelResize({ workspace: ui.workspace, details: ui.details,
      divider: ui["panel-divider"], documentRef, windowRef, storage,
      storageKey: state.config.storageKey, onResize: hideHover });
    selectRegion(null);
    await renderPage();
    ui["toggle-reading"].disabled = false;
    ui["find-toggle"].disabled = false;
  } catch (error) {
    ui.workspace.hidden = true;
    setStatus("bad", "PDF not verified");
    showError(error);
  }

  ui["find-toggle"].addEventListener("click", () => {
    showSearch();
    if (ui["find-input"].value && !state.search.matches.length) return runSearch();
  });
  ui["find-close"].addEventListener("click", closeSearch);
  ui["find-previous"].addEventListener("click", () => showMatch(state.search.active - 1));
  ui["find-next"].addEventListener("click", () => showMatch(state.search.active + 1));
  ui["find-input"].addEventListener("input", () => {
    ++state.search.epoch;
    clearTimer(state.search.timer);
    state.search.matches = [];
    state.search.busy = true;
    ui["find-highlights"].replaceChildren();
    ui["find-previous"].disabled = ui["find-next"].disabled = true;
    ui["find-count"].textContent = ui["find-input"].value.trim() ? "Searching…" : "";
    if (!searchDelay || !ui["find-input"].value.trim()) return runSearch();
    state.search.timer = setTimer(runSearch, searchDelay);
  });
  ui["previous-page"].addEventListener("click", () => changePage(state.currentPage - 1));
  ui["next-page"].addEventListener("click", () => changePage(state.currentPage + 1));
  ui["page-number"].addEventListener("change", () => changePage(ui["page-number"].value));
  ui["zoom-out"].addEventListener("click", () => changeZoom(state.zoom - 10));
  ui["zoom-in"].addEventListener("click", () => changeZoom(state.zoom + 10));
  ui["rotate-right"].addEventListener("click", () => rotate(90));
  ui["toggle-reading"].addEventListener("click", () => showReadingMode(!state.readingMode));
  ui["toggle-faithfulness"].addEventListener("click", () => showFaithfulness(!state.faithfulness));
  ui["back-to-page"].addEventListener("click", backToPage);
  ui["note-toggle"].addEventListener("click", () => {
    state.noteOpen = !state.noteOpen;
    renderNote(state.selected);
  });
  ui["step-parent"].addEventListener("click", async () => {
    await openItem(state.selected?.stepOf);
    focusQuietly(ui["item-label"]);
  });
  for (const [id, delta, other] of [["previous-item", -1, "next-item"], ["next-item", 1, "previous-item"]]) {
    ui[id].addEventListener("click", async () => {
      await stepItem(delta);
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
  // The status chip's note and an open hover card close on a click anywhere else.
  documentRef.addEventListener("click", (event) => {
    if (ui["status-chip"].open && !ui["status-chip"].contains?.(event.target)) {
      ui["status-chip"].open = false;
    }
    const within = (node) => node === event.target || Boolean(node?.contains?.(event.target));
    if (hover.token && !within(hover.token) && !within(ui["hover-card"])) {
      hideHover();
    }
  });
  // The pointer may move onto the hover card (to read or scroll it); the card stays while it is there.
  ui["hover-card"].addEventListener("pointerenter", (event) => {
    if (event.pointerType !== "touch") {
      hover.over = true;
      cancelHideHover();
    }
  });
  ui["hover-card"].addEventListener("pointerleave", (event) => {
    if (event.pointerType !== "touch") {
      hover.over = false;
      if (hover.token) hideHoverSoon();
    }
  });
  // A tap on the card (a tap is never `over` it) blurs its token after the finger lifts, and the
  // card stays. The tap ends with its click, or with a pointercancel when it scrolls the card instead.
  ui["hover-card"].addEventListener("pointerdown", (event) => {
    if (event.pointerType === "touch") {
      hover.pressed = true;
    }
  });
  for (const type of ["click", "pointercancel"]) {
    ui["hover-card"].addEventListener(type, () => {
      hover.pressed = false;
    });
  }
  documentRef.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "f"
        && state.search.index && !ui.workspace.hidden) {
      event.preventDefault();
      showSearch();
      if (ui["find-input"].value && !state.search.matches.length) return runSearch();
      return;
    }
    if (!ui["find-bar"].hidden && !event.metaKey && !event.ctrlKey && !event.altKey) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeSearch();
        return;
      }
      if (event.key === "Enter" && event.target === ui["find-input"]) {
        event.preventDefault();
        if (state.search.timer) return runSearch();
        return showMatch(state.search.active + (event.shiftKey ? -1 : 1));
      }
    }
    if (event.defaultPrevented || event.target === ui["panel-divider"] || !isViewerShortcut(event)) {
      return;
    }
    if (event.key === "ArrowLeft") changePage(state.currentPage - 1);
    else if (event.key === "ArrowRight") changePage(state.currentPage + 1);
    else if (event.key === "+" || event.key === "=") changeZoom(state.zoom + 10);
    else if (event.key === "-") changeZoom(state.zoom - 10);
    else if (event.key.toLowerCase() === "r") rotate(90);
    else if (event.key === "Escape") {
      // A card its token's scroll has hidden is not one to close: Escape leaves the item.
      if (state.readingMode) {
        showReadingMode(false);
        focusQuietly(ui["toggle-reading"]);
      } else if (hover.token && !ui["hover-card"].hidden) hideHover();
      else if (state.selected && ui.details.contains?.(documentRef.activeElement)) backToPage();
      else selectRegion(null);
    }
  });
  // An open hover card follows its token when anything scrolls or the window resizes.
  documentRef.addEventListener("scroll", placeHover, { capture: true, passive: true });
  windowRef?.addEventListener?.("resize", placeHover);
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
