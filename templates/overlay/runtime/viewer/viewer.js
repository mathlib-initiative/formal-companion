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
  buildIndex,
  cardSummaryText,
  cardsMatching,
  declarationVerdictDisplay,
  groupCards,
  paginateCards,
  parseBundle,
  preferredItemId,
  provenanceText,
  quadToViewport,
  readConfig,
  readAnnotationsBlock,
  sha256Hex,
  verdictNote,
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
      "error", "status", "notice", "workspace",
      "previous-page", "next-page", "page-number", "page-count",
      "zoom-out", "zoom-in", "zoom", "zoom-value",
      "rotate-left", "rotate-right", "rotation-value",
      "page-stage", "pdf-canvas", "overlay",
      "selection-empty", "selection", "item-choices", "item-label", "item-locator",
      "verdict-notes", "coverage-note", "provenance-note", "fallback-note",
      "role-filter", "role-filter-wrap", "target-filter",
      "target-summary", "target-groups",
      "previous-targets", "next-targets", "target-page",
    ].map((id) => [id, element(documentRef, id)]),
  );
  const state = {
    cardPage: 1,
    config: null,
    currentPage: 1,
    index: null,
    pdfDocument: null,
    region: null,
    renderEpoch: 0,
    renderTask: null,
    rotation: 0,
    selected: null,
    zoom: 100,
  };

  function showError(error) {
    ui.error.textContent = error instanceof Error ? error.message : String(error);
    ui.error.hidden = false;
  }

  function clearError() {
    ui.error.hidden = true;
    ui.error.textContent = "";
  }

  function setNotice(message) {
    ui.notice.hidden = !message;
    ui.notice.textContent = message ?? "";
  }

  function applyConfig(config) {
    // Every per-companion string on the shell, written as text, never as markup.
    documentRef.title = config.title;
    ui["page-heading"].textContent = config.title;
    ui["page-lede"].textContent = config.lede;
    ui["page-lede"].hidden = !config.lede;
    ui["back-link"].textContent = config.backLink.text;
    ui["back-link"].setAttribute("href", config.backLink.href);
  }

  function setOptions(select, values) {
    select.replaceChildren(new Option("All", ""));
    for (const value of [...new Set(values.filter(Boolean))].sort()) {
      select.append(new Option(value, value));
    }
  }

  function renderVerdictNotes(item) {
    ui["verdict-notes"].replaceChildren();
    for (const verdict of item.verdicts) {
      const shown = verdictNote(verdict);
      const note = documentRef.createElement("p");
      note.className = shown.className;
      note.title = shown.title;
      note.textContent = shown.text;
      ui["verdict-notes"].append(note);
    }
  }

  function renderItemChoices(region) {
    ui["item-choices"].replaceChildren();
    const items = region ? region.itemIds : [];
    ui["item-choices"].hidden = items.length < 2;
    if (items.length < 2) {
      return;
    }
    for (const itemId of items) {
      const item = state.index.items.get(itemId);
      const button = documentRef.createElement("button");
      button.type = "button";
      button.className = "item-choice";
      button.dataset.itemId = itemId;
      button.textContent = item.label;
      button.setAttribute("aria-pressed", String(state.selected?.id === itemId));
      button.addEventListener("click", () => selectItem(itemId));
      ui["item-choices"].append(button);
    }
  }

  function selectRegion(region) {
    state.region = region;
    for (const polygon of ui.overlay.querySelectorAll?.(".overlay-region") ?? []) {
      polygon.setAttribute("aria-pressed", String(region?.id === polygon.dataset.regionId));
    }
    renderItemChoices(region);
    selectItem(region ? preferredItemId(region, state.index.items) : null);
  }

  function selectItem(itemId) {
    const item = itemId ? state.index.items.get(itemId) ?? null : null;
    state.selected = item;
    state.cardPage = 1;
    ui["selection-empty"].hidden = Boolean(item);
    ui.selection.hidden = !item;
    for (const button of ui["item-choices"].children ?? []) {
      button.setAttribute?.("aria-pressed", String(button.dataset?.itemId === itemId));
    }
    if (!item) {
      return;
    }
    ui["item-label"].textContent = item.label;
    ui["item-locator"].textContent = item.locator;
    renderVerdictNotes(item);
    ui["coverage-note"].hidden = !item.coverageNote;
    ui["coverage-note"].textContent = item.coverageNote;
    const provenance = provenanceText(item);
    ui["provenance-note"].hidden = !provenance;
    ui["provenance-note"].textContent = provenance;
    const clusters = item.cards.length ? [] : item.clusterIds;
    ui["fallback-note"].hidden = !clusters.length;
    ui["fallback-note"].textContent = clusters.length
      ? `No declaration is linked to this item; its ledger cluster: ${clusters.join(", ")}.`
      : "";
    setOptions(ui["role-filter"], item.roles);
    ui["role-filter"].value = "";
    ui["role-filter-wrap"].hidden = item.roles.length < 2;
    ui["target-filter"].value = "";
    renderCards();
  }

  function cardElement(card) {
    const row = documentRef.createElement("li");
    row.className = "target-card";
    const title = documentRef.createElement(card.href ? "a" : "span");
    title.textContent = card.name;
    title.title = card.fullName;
    if (card.href) {
      title.setAttribute("href", card.href);
    }
    row.append(title);
    for (const badge of card.badges) {
      const mark = documentRef.createElement("span");
      mark.className = "target-badge";
      mark.textContent = badge;
      row.append(mark);
    }
    if (card.verdict) {
      const display = declarationVerdictDisplay(card.verdict.verdict, card.verdict.status);
      const mark = documentRef.createElement("span");
      mark.className = `target-badge verdict-badge ${display.className}`;
      mark.textContent = display.label;
      mark.title = card.verdict.note;
      row.append(mark);
    }
    const meta = [card.kind, card.module, card.relation, card.fallbackLabel, card.note]
      .filter(Boolean)
      .join(" · ");
    if (meta) {
      const line = documentRef.createElement("span");
      line.className = "target-meta";
      line.textContent = meta;
      row.append(line);
    }
    if (card.snippet?.text) {
      const pre = documentRef.createElement("pre");
      pre.className = "target-snippet";
      const code = documentRef.createElement("code");
      code.textContent = card.snippet.text;
      pre.append(code);
      row.append(pre);
    }
    return row;
  }

  function renderCards() {
    const item = state.selected;
    if (!item) {
      return;
    }
    const matched = cardsMatching(item.cards, {
      query: ui["target-filter"].value,
      role: ui["role-filter"].value,
    });
    const page = paginateCards(matched, state.cardPage, CARD_PAGE_SIZE);
    state.cardPage = page.page;
    ui["target-summary"].className = item.coverage
      ? `target-summary coverage-summary coverage-${item.coverage}`
      : "target-summary";
    ui["target-summary"].textContent = cardSummaryText({ coverage: item.coverage, shown: page.total });
    ui["target-page"].textContent = `Page ${page.page} of ${page.pageCount}`;
    ui["previous-targets"].disabled = page.page <= 1;
    ui["next-targets"].disabled = page.page >= page.pageCount;
    ui["target-groups"].replaceChildren();
    for (const group of groupCards(page.items)) {
      const section = documentRef.createElement("section");
      section.className = "target-group";
      const heading = documentRef.createElement("h4");
      heading.textContent = group.role || "unclassified";
      const list = documentRef.createElement("ul");
      list.className = "target-list";
      list.append(...group.cards.map(cardElement));
      section.append(heading, list);
      ui["target-groups"].append(section);
    }
  }

  function renderOverlay(displayed, viewport) {
    ui.overlay.replaceChildren();
    ui.overlay.setAttribute("viewBox", `0 0 ${viewport.width} ${viewport.height}`);
    ui.overlay.setAttribute("width", String(viewport.width));
    ui.overlay.setAttribute("height", String(viewport.height));
    for (const region of state.index.pages.get(state.currentPage) ?? []) {
      for (const quad of region.quads) {
        const polygon = documentRef.createElementNS("http://www.w3.org/2000/svg", "polygon");
        polygon.classList.add("overlay-region");
        polygon.dataset.regionId = region.id;
        polygon.setAttribute(
          "points",
          quadToViewport(quad, displayed, viewport).map(([x, y]) => `${x},${y}`).join(" "),
        );
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
        ui.overlay.append(polygon);
      }
    }
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
    renderOverlay(displayed, viewport);
    ui["page-number"].value = String(state.currentPage);
    ui["page-count"].textContent = `of ${state.pdfDocument.numPages}`;
    ui["previous-page"].disabled = state.currentPage <= 1;
    ui["next-page"].disabled = state.currentPage >= state.pdfDocument.numPages;
    ui["zoom-value"].textContent = `${state.zoom}%`;
    ui["rotation-value"].textContent = `${state.rotation}°`;
    ui.status.textContent =
      `Verified PDF · page ${state.currentPage} of ${state.pdfDocument.numPages}`;
    rememberPage(state.config.storageKey, state.currentPage, { historyRef, storage });
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
    ui.zoom.value = String(state.zoom);
    await renderPage();
  }

  async function rotate(delta) {
    state.rotation = (state.rotation + delta + 360) % 360;
    await renderPage();
  }

  // A refusal is an error message on the page, never a blank page: the two data
  // files are loaded inside this guard so the shell always says what went wrong.
  try {
    ui.status.textContent = "Loading the overlay configuration…";
    state.config = await loadConfig(fetchImpl, configUrl);
    applyConfig(state.config);
    ui.status.textContent = "Loading the annotations…";
    state.index = (await loadAnnotations(state.config, fetchImpl, cryptoProvider, pageUrl, generatedUrl)).index;
  } catch (error) {
    ui.workspace.hidden = true;
    ui.status.textContent = "The overlay data was not loaded.";
    showError(error);
    throw error;
  }
  // A region the document places off the page, or in pt on a page whose size it
  // never declares, is dropped rather than guessed at; the page says how many.
  setNotice(state.index.warnings.length
    ? `${state.index.warnings.length} region(s) could not be placed and are not shown. `
      + state.index.warnings[0]
    : "");
  ui.status.textContent = "Overlay data read. Loading and verifying the loopback PDF…";
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
    ui.status.textContent = "The verified loopback PDF was not opened.";
    showError(error);
  }

  ui["previous-page"].addEventListener("click", () => changePage(state.currentPage - 1));
  ui["next-page"].addEventListener("click", () => changePage(state.currentPage + 1));
  ui["page-number"].addEventListener("change", () => changePage(ui["page-number"].value));
  ui.zoom.addEventListener("input", () => changeZoom(ui.zoom.value));
  ui["zoom-out"].addEventListener("click", () => changeZoom(state.zoom - 10));
  ui["zoom-in"].addEventListener("click", () => changeZoom(state.zoom + 10));
  ui["rotate-left"].addEventListener("click", () => rotate(-90));
  ui["rotate-right"].addEventListener("click", () => rotate(90));
  for (const filter of [ui["role-filter"], ui["target-filter"]]) {
    filter.addEventListener("input", () => {
      state.cardPage = 1;
      renderCards();
    });
  }
  ui["previous-targets"].addEventListener("click", () => {
    state.cardPage -= 1;
    renderCards();
  });
  ui["next-targets"].addEventListener("click", () => {
    state.cardPage += 1;
    renderCards();
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
