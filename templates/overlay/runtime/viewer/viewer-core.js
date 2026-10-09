/**
 * The overlay viewer's pure functions: the shard list, the Literature Links
 * records, coordinate conversion, the href rules and sha256 verification.
 *
 * Nothing here touches the DOM, so `node --test` exercises all of it; viewer.js
 * holds the controller. Nothing here is specific to one companion either: every
 * per-book value arrives as data, from `overlay.config.json`
 * (docs/overlay.md section 3), from `GENERATED.json`'s shard list and from the
 * shards, and a value this module cannot read is an error or a warning with a
 * message, never a guess.
 */

// --- the files --------------------------------------------------------------

export const CONFIG_SCHEMA = "formal-companion/overlay-config";
export const CONFIG_SCHEMA_VERSION = 1;
export const CONFIG_URL = "../overlay.config.json";
/** The shard list: `GENERATED.json`'s `annotations` block (docs/overlay.md section 2). */
export const GENERATED_URL = "../GENERATED.json";
/** The extension key the overlay's records carry its own facts under. */
export const NAMESPACE = "org.mathlib-initiative.formal-companion.overlay";

/** The viewer never reads a JSON file larger than this. */
export const MAX_JSON_BYTES = 64 * 1024 * 1024;

/** `pdf.route` must start with this and carry no `..` (the contract's section 3). */
export const ROUTE_PREFIX = "/__";

export const DEFAULT_BACK_LINK = Object.freeze({ href: "../../index.html", text: "Back to the site" });
export const DEFAULT_MOUNT = "pdf-overlay";
export const DEFAULT_STORAGE_KEY = "formal-companion-overlay-page";

/** How many declaration cards one page of the card list holds. */
export const CARD_PAGE_SIZE = 25;

// --- vocabulary -------------------------------------------------------------

/** A link's role, in the order the card list shows them (the contract's section 4). */
export const LINK_ROLES = Object.freeze(["key", "attributed", "support", "example", "helper"]);

/** A verdict's `verdict`, and each declaration's. */
export const VERDICT_KINDS = Object.freeze([
  "faithful",
  "convention",
  "attribution_gap",
  "proxy",
  "deviation",
]);
/** The label each verdict kind shows; viewer.css has a colour for each one. */
export const VERDICT_LABELS = Object.freeze({
  attribution_gap: "attribution gap",
  convention: "convention delta",
  deviation: "statement deviation",
  faithful: "faithful",
  proxy: "proxy definition",
});

/**
 * One generic line per status: what the item's links do NOT show on their
 * own. `formalized`, `partial` and `not_formalized` are Literature Links'
 * recommended values; the others are the curation classes the exporter writes
 * verbatim. No value is ever guessed: an unknown one shows no summary.
 */
export const COVERAGE_SUMMARIES = Object.freeze({
  example_only: "Formalised only by anonymous examples; no named declaration is attributed to it.",
  formalized: "Formalised in this companion: a key declaration states it.",
  not_formalized: "Not formalised in this companion.",
  out_of_scope: "Outside the scope this companion set itself.",
  partial: "Formalised only in part: the attributed declarations state a special case or one part of it.",
  private_only: "Stated only by a private declaration.",
  support_only: "Supported only by declarations that state a neighbouring item.",
});

/** Every status `COVERAGE_SUMMARIES` has a line for, sorted. */
export const COVERAGE_VALUES = Object.freeze(Object.keys(COVERAGE_SUMMARIES).sort());

// --- small helpers ----------------------------------------------------------

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function text(value) {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function fail(message) {
  throw new Error(message);
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/** The overlay's own extension object of a record, a source or a target, or {}. */
export function extensionOf(node) {
  const value = isObject(node) && isObject(node.extensions) ? node.extensions[NAMESPACE] : null;
  return isObject(value) ? value : {};
}

// --- hashing ----------------------------------------------------------------

export function bytesToHex(bytes) {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(bytes, cryptoProvider = globalThis.crypto) {
  if (!cryptoProvider?.subtle) {
    throw new Error("WebCrypto is unavailable; the overlay data cannot be verified safely.");
  }
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const digest = await cryptoProvider.subtle.digest(
    "SHA-256",
    view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength),
  );
  return bytesToHex(new Uint8Array(digest));
}

/**
 * Compare the bytes with the sha256 the records' book pin states.
 * The expected value is a parameter: no hash of any one PDF lives in a template.
 */
export async function verifyPdfBytes(bytes, expected, cryptoProvider = globalThis.crypto) {
  const want = text(expected).toLowerCase();
  if (!/^[a-f\d]{64}$/.test(want)) {
    throw new Error("The source's sha256 is not 64 hex digits; the PDF cannot be verified.");
  }
  const actual = await sha256Hex(bytes, cryptoProvider);
  return { actual, expected: want, ok: actual === want };
}

// --- hrefs ------------------------------------------------------------------

const SCHEME = /^[a-z][a-z\d+.-]*:/i;

/** An `https:` URL with no fragment of its own, or null. */
export function safeHttpsUrl(value) {
  const url = typeof value === "string" ? value : "";
  return /^https:\/\/[^\s#\\]+$/.test(url) ? url : null;
}

/**
 * The back link's own `href`, or null. It is the one href the config supplies,
 * and its default (`../../index.html`) climbs out of the mount: a relative path
 * is accepted with its `..` segments and an `https:` URL whole, but a
 * scheme-bearing (`javascript:`, `data:`), protocol-relative or root-absolute
 * value is refused so the header can never carry a live link the config
 * smuggled in.
 */
export function safeBackLinkHref(value) {
  const href = text(value);
  if (!href) {
    return null;
  }
  if (safeHttpsUrl(href)) {
    return href;
  }
  if (/[\s#]/.test(href) || href.includes("\\") || href.startsWith("/") || SCHEME.test(href)) {
    return null;
  }
  return href;
}

/**
 * Where a record's `target` leads, or null. Literature Links resolves a
 * relative reference against the URL of the shard that holds the record
 * (the specification's "References"), so `baseUrl` is the shard's URL. The
 * result is used only when it is an `https:` URL or an `http(s):` URL of the
 * page's own origin: a `javascript:`, `data:` or foreign plain-`http:` target
 * is never a link.
 */
export function targetHref(target, baseUrl, pageOrigin) {
  const uri = typeof target === "string" ? target : isObject(target) ? target.uri : null;
  if (typeof uri !== "string" || !uri || /[\s\\]/.test(uri)) {
    return null;
  }
  let resolved;
  try {
    resolved = new URL(uri, baseUrl);
  } catch {
    return null;
  }
  if (resolved.username || resolved.password) {
    return null;
  }
  if (resolved.protocol === "https:") {
    return resolved.href;
  }
  if (resolved.protocol === "http:" && resolved.origin === pageOrigin) {
    return resolved.href;
  }
  return null;
}

// --- the configuration ------------------------------------------------------

/** `../` once per directory between `<mount>/viewer/` and the site root. */
export function siteRootFor(mount) {
  const parts = text(mount).split("/").filter(Boolean);
  if (!parts.length || parts.some((part) => part === "." || part === "..")) {
    fail(`overlay.config.json has site.mount ${JSON.stringify(mount)}; a mount is a relative path.`);
  }
  return "../".repeat(parts.length + 1);
}

/**
 * Read `overlay.config.json`: check what the viewer relies on, and fill in the
 * defaults of docs/overlay.md section 3 for everything else. The full rule set
 * is `tools/check_generated.py`'s; this is the subset the page needs before it
 * will fetch anything.
 */
export function readConfig(raw) {
  if (!isObject(raw)) {
    fail("overlay.config.json must be a JSON object.");
  }
  if (raw.schema !== CONFIG_SCHEMA) {
    fail(`overlay.config.json has schema ${JSON.stringify(raw.schema)}; expected "${CONFIG_SCHEMA}".`);
  }
  if (raw.schema_version !== CONFIG_SCHEMA_VERSION) {
    fail(`overlay.config.json has schema_version ${JSON.stringify(raw.schema_version)}; `
         + `expected ${CONFIG_SCHEMA_VERSION}.`);
  }
  const sourceId = text(raw.source_id);
  if (!sourceId) {
    fail("overlay.config.json names no source_id.");
  }
  const title = text(raw.title);
  if (!title) {
    fail("overlay.config.json names no title.");
  }
  const route = text(isObject(raw.pdf) ? raw.pdf.route : "");
  if (!route.startsWith(ROUTE_PREFIX) || route.includes("..")) {
    fail(`overlay.config.json has pdf.route ${JSON.stringify(route)}; a route starts with `
         + `"${ROUTE_PREFIX}" and carries no "..".`);
  }
  const backLink = isObject(raw.back_link) ? raw.back_link : DEFAULT_BACK_LINK;
  const backHref = safeBackLinkHref(backLink.href) ?? DEFAULT_BACK_LINK.href;
  const display = isObject(raw.display) ? raw.display : {};
  const site = isObject(raw.site) ? raw.site : {};
  return {
    backLink: { href: backHref, text: text(backLink.text) || DEFAULT_BACK_LINK.text },
    lede: text(raw.lede),
    pdfRoute: route,
    // build_site.py publishes the shards at the site root, the same place
    // relative to the built pages as in the repository; it is this far up.
    siteRoot: siteRootFor(site.mount ?? DEFAULT_MOUNT),
    sourceId,
    storageKey: text(raw.storage_key) || DEFAULT_STORAGE_KEY,
    stripPrefix: text(display.strip_prefix),
    title,
  };
}

// --- the shard list and the shards ------------------------------------------

const SHARD_NAME = /^[0-9A-Za-z][0-9A-Za-z._-]*\.jsonl$/;

function safeRelative(value) {
  return typeof value === "string" && value !== "" && !value.includes("\\")
    && !value.startsWith("/") && value.split("/").every((part) => part && part !== "." && part !== "..");
}

/**
 * The shards `GENERATED.json` lists: `[{path, sha256}]`, each path inside the
 * recorded directory. A browser cannot list a directory, so this list is how
 * the viewer finds them, and each sha256 is checked when the shard arrives.
 */
export function readShardList(generated) {
  const block = isObject(generated) ? generated.annotations : null;
  if (!isObject(block)) {
    fail("GENERATED.json has no annotations block; `cf overlay export` and `cf overlay sync` write it.");
  }
  if (!safeRelative(block.directory)) {
    fail("GENERATED.json names no relative annotations.directory.");
  }
  const shards = asArray(block.shards).map((row, index) => {
    const path = isObject(row) ? row.path : null;
    const sha256 = isObject(row) ? text(row.sha256) : "";
    const name = typeof path === "string" ? path.slice(block.directory.length + 1) : "";
    if (!safeRelative(path) || !path.startsWith(`${block.directory}/`) || !SHARD_NAME.test(name)
        || !/^[a-f\d]{64}$/.test(sha256)) {
      fail(`GENERATED.json annotations.shards[${index}] is not {path: <directory>/<name>.jsonl, sha256}.`);
    }
    return { path, sha256 };
  });
  if (!shards.length) {
    fail("GENERATED.json lists no shard; `cf overlay export` writes them.");
  }
  return { directory: block.directory, shards };
}

/** The annotations of one JSONL shard, one per nonblank line. */
export function parseShard(source, label) {
  const records = [];
  source.split("\n").forEach((line, index) => {
    if (!line.replace(/[ \t\r]/g, "")) {
      return;
    }
    let value;
    try {
      value = JSON.parse(line);
    } catch (error) {
      fail(`${label}:${index + 1} is not JSON (${error instanceof Error ? error.message : error}).`);
    }
    if (!isObject(value)) {
      fail(`${label}:${index + 1} is not an annotation object.`);
    }
    records.push(value);
  });
  return records;
}

// --- geometry (Literature Links' PDF location) -------------------------------

const EPSILON = 1e-9;

function fraction(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

/**
 * `{page, quads}` of a record's PDF location: its `rect`, `[x, y, width,
 * height]` as fractions of the displayed page from the top left, or the
 * extension's quads when the region was drawn as quadrilaterals. A location
 * this viewer cannot place throws; the caller drops the record and says so.
 */
export function recordRegion(record) {
  const location = isObject(record) ? record.location : null;
  const page = isObject(location) ? location.page : undefined;
  if (!Number.isInteger(page) || page < 1) {
    fail("A record has no physical page.");
  }
  const rect = location.rect;
  if (!Array.isArray(rect) || rect.length !== 4 || !rect.every(fraction)) {
    fail(`The record on page ${page} has no rect of four page fractions.`);
  }
  const [x, y, width, height] = rect;
  if (!(width > 0 && height > 0) || x + width > 1 + EPSILON || y + height > 1 + EPSILON) {
    fail(`The record on page ${page} has a rect with no area or off the page.`);
  }
  const quads = asArray(extensionOf(record).quads);
  if (quads.length) {
    return {
      page,
      quads: quads.map((quad) => {
        if (!Array.isArray(quad) || quad.length !== 8 || !quad.every(fraction)) {
          fail(`The record on page ${page} has a malformed quad.`);
        }
        return [0, 2, 4, 6].map((n) => [quad[n], quad[n + 1]]);
      }),
    };
  }
  return { page, quads: [[[x, y], [x + width, y], [x + width, y + height], [x, y + height]]] };
}

/**
 * A point in fractions of the displayed page (top-left origin, after the
 * page's own rotation) placed in a PDF.js viewport. `displayed` is the page's
 * viewport at its own rotation and scale 1; its `convertToPdfPoint` takes the
 * point back to PDF space, whatever extra rotation `viewport` carries.
 */
export function pointToViewport(point, displayed, viewport) {
  const [x, y] = Array.isArray(point) ? point.map(Number) : [NaN, NaN];
  if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || x > 1 || y < 0 || y > 1) {
    throw new Error("Overlay coordinates must be finite page fractions in [0, 1].");
  }
  if (!(displayed?.width > 0) || !(displayed?.height > 0)
      || typeof displayed.convertToPdfPoint !== "function") {
    throw new Error("The displayed page viewport is invalid.");
  }
  if (typeof viewport?.convertToViewportPoint !== "function") {
    throw new Error("A PDF.js viewport is required for overlay transformation.");
  }
  const [px, py] = displayed.convertToPdfPoint(x * displayed.width, y * displayed.height);
  return viewport.convertToViewportPoint(px, py);
}

export function quadToViewport(quad, displayed, viewport) {
  if (!Array.isArray(quad) || quad.length !== 4) {
    throw new Error("An overlay quadrilateral must contain four points.");
  }
  return quad.map((point) => pointToViewport(point, displayed, viewport));
}

// --- verdict display --------------------------------------------------------

/**
 * The item-level verdict note. A `fixed` entry is history, not a live finding:
 * it keeps its kind in the label so the record stays legible, and drops the
 * kind's warning colour.
 */
export function verdictDisplay(verdict, status) {
  const label = VERDICT_LABELS[verdict] ?? verdict;
  return status === "fixed"
    ? { className: "verdict-fixed", label: `fixed: ${label}` }
    : { className: `verdict-${verdict}`, label };
}

/**
 * The badge on one declaration card. Under a fixed verdict a declaration is
 * either repaired in place (its verdict is then `faithful`) or left as it was,
 * with the fix in another declaration; the card still shows the old one, which
 * still deviates, so it keeps its kind and colour and says it is superseded.
 */
export function declarationVerdictDisplay(verdict, status) {
  const label = VERDICT_LABELS[verdict] ?? verdict;
  if (verdict === "faithful") {
    return { className: "verdict-faithful", label };
  }
  return status === "fixed"
    ? { className: `verdict-${verdict}`, label: `${label}, superseded` }
    : { className: `verdict-${verdict}`, label };
}

/**
 * The item-level verdict note: its kind, severity and summary, then
 * `[<method>, <status>; <resolution>]`. The method is the adjudication's, from
 * the verdict's provenance, and stays on purpose: these verdicts are machine
 * adjudications until a human confirms them or a merged fix supersedes them.
 * The title (a tooltip) is the evidence and the provenance's note.
 */
export function verdictNote(verdict) {
  const display = verdictDisplay(verdict.verdict, verdict.status);
  const severity = text(verdict.severity) && verdict.severity !== "none" ? ` (${verdict.severity})` : "";
  const provenance = isObject(verdict.provenance) ? verdict.provenance : {};
  const status = text(verdict.status).replace(/_/g, " ");
  const resolution = resolutionText(verdict.resolution);
  const tail = [text(provenance.method), status].filter(Boolean).join(", ") + (resolution ? `; ${resolution}` : "");
  return {
    className: `verdict-note ${display.className}`,
    text: `Faithfulness: ${display.label}${severity}. ${text(verdict.summary)} [${tail}]`,
    title: [...asArray(verdict.evidence), text(provenance.note)].filter((line) => text(line)).join("\n"),
  };
}

/** The one line a verdict's resolution adds, or "" when the verdict is open. */
export function resolutionText(resolution) {
  if (!isObject(resolution) || !text(resolution.state) || resolution.state === "open") {
    return "";
  }
  const parts = [resolution.state.replace(/_/g, " ")];
  if (Number.isInteger(resolution.pr)) {
    parts.push(`PR #${resolution.pr}`);
  }
  if (Number.isInteger(resolution.issue)) {
    parts.push(`issue #${resolution.issue}`);
  }
  if (text(resolution.branch)) {
    parts.push(`branch ${text(resolution.branch)}`);
  }
  if (text(resolution.note)) {
    parts.push(text(resolution.note));
  }
  return parts.join(", ");
}

// --- coverage and summaries -------------------------------------------------

/** The generic one-line summary of a status, or "" for an unknown one. */
export function coverageSummary(coverage) {
  return COVERAGE_SUMMARIES[coverage] ?? "";
}

/** The line above the card list: the status speaks for the item, else a count. */
export function cardSummaryText({ coverage = null, shown = 0 }) {
  const summary = coverage ? coverageSummary(coverage) : "";
  if (summary) {
    return summary;
  }
  return shown ? `${plural(shown, "linked declaration")}.` : "No declaration is linked to this item.";
}

// --- the index --------------------------------------------------------------

function statusValue(status) {
  return isObject(status) ? text(status.value) : text(status);
}

function pageList(pages) {
  const list = asArray(pages).filter(Number.isInteger);
  return list.length ? `${list.length === 1 ? "p." : "pp."} ${list.join(", ")}` : "";
}

/** Where the item is in the source: its locator text, else its printed and physical pages. */
export function locatorText(locator) {
  if (!isObject(locator)) {
    return "";
  }
  if (text(locator.text)) {
    return text(locator.text);
  }
  const printed = pageList(locator.printed);
  const physical = pageList(locator.physical);
  return [printed && `printed ${printed}`, physical && `PDF ${physical}`].filter(Boolean).join(", ");
}

/** `<method>, confirmed|unconfirmed` of a link's provenance, or "". */
export function provenanceLabel(provenance) {
  if (!isObject(provenance) || !text(provenance.method)) {
    return "";
  }
  const confirmed = typeof provenance.confirmed_by_human === "boolean"
    ? `, ${provenance.confirmed_by_human ? "confirmed" : "unconfirmed"}` : "";
  return `${text(provenance.method)}${confirmed}`;
}

/**
 * The line naming how the item's links were made: the key and attributed
 * counts when it has a key card, and each distinct link provenance.
 */
export function provenanceText(item) {
  const cards = asArray(item?.cards);
  const labels = [...new Set(cards.map((entry) => entry.provenance).filter(Boolean))];
  if (!labels.length) {
    return "";
  }
  const keys = cards.filter((entry) => entry.role === "key").length;
  const attributed = cards.filter((entry) => entry.role === "key" || entry.role === "attributed").length;
  return keys
    ? `${plural(keys, "key declaration")} for this result (${attributed} attributed); ${labels.join("; ")}.`
    : `Links: ${labels.join("; ")}.`;
}

/** The item a click on the region opens: the first that has a card, else the first. */
export function preferredItemId(region, items) {
  const ids = asArray(region?.itemIds);
  return ids.find((id) => asArray(items?.get(id)?.cards).length) ?? ids[0] ?? null;
}

function stripPrefix(name, prefix) {
  return prefix && name.startsWith(prefix) ? name.slice(prefix.length) : name;
}

function card(record, ext, context) {
  const target = record.target;
  const pin = isObject(target) && isObject(target.pin) ? target.pin : {};
  const declaration = isObject(ext.declaration) ? ext.declaration : {};
  const destination = isObject(ext.destination) ? ext.destination : null;
  const uri = typeof target === "string" ? target : text(target?.uri);
  const fullName = text(pin.declaration) || text(declaration.id) || uri;
  // The overlay's own records say whether the target is a rendered page; a
  // record from elsewhere does not, and its target is taken as one.
  const ours = Boolean(text(ext.role));
  const href = !ours || destination ? targetHref(target, context.baseUrl, context.pageOrigin) : null;
  const badges = [];
  if (declaration.private) {
    badges.push("private");
  }
  if (declaration.anonymous) {
    badges.push("anonymous");
  }
  if (text(declaration.sorry) && declaration.sorry !== "none") {
    badges.push(`sorry: ${declaration.sorry}`);
  }
  if (destination?.resolution === "fallback") {
    badges.push("fallback");
  }
  if (!href) {
    badges.push("unlinked");
  }
  const snippet = isObject(ext.snippet) && text(ext.snippet.text)
    ? { line: Number.isInteger(ext.snippet.line) ? ext.snippet.line : null, text: ext.snippet.text }
    : null;
  const verdict = isObject(ext.declaration_verdict) && text(ext.declaration_verdict.verdict)
    ? {
      note: text(ext.declaration_verdict.note),
      status: text(ext.declaration_verdict.status),
      verdict: text(ext.declaration_verdict.verdict),
    }
    : null;
  return {
    badges,
    declarationId: text(declaration.id) || fullName,
    fallbackLabel: destination?.resolution === "fallback" ? text(destination.fallback_label) : "",
    fullName,
    href,
    id: text(record.id),
    kind: text(declaration.kind),
    module: text(pin.module),
    name: stripPrefix(fullName, context.stripPrefix) || fullName,
    provenance: provenanceLabel(ext.provenance),
    relation: text(ext.relation),
    resolution: text(destination?.resolution),
    role: text(ext.role),
    snippet,
    verdict,
  };
}

function orderCards(cards) {
  return cards
    .map((entry, index) => ({ entry, index }))
    .sort((left, right) => {
      const a = LINK_ROLES.indexOf(left.entry.role);
      const b = LINK_ROLES.indexOf(right.entry.role);
      const rank = (a < 0 ? LINK_ROLES.length : a) - (b < 0 ? LINK_ROLES.length : b);
      return rank !== 0 ? rank : left.index - right.index;
    })
    .map(({ entry }) => entry);
}

function ofSource(record, sourceId) {
  return isObject(record) && isObject(record.source) && record.source.type === "pdf"
    && extensionOf(record.source).source_id === sourceId;
}

/**
 * The PDF the config names: the `source` of the records whose overlay
 * extension carries its `source_id`. Every such record must cite the same
 * bytes; two different sha256 under one id is a refusal.
 */
export function pdfSource(shards, sourceId) {
  let found = null;
  for (const shard of asArray(shards)) {
    for (const record of asArray(shard.records)) {
      if (!ofSource(record, sourceId)) {
        continue;
      }
      const pin = isObject(record.source.pin) ? record.source.pin : {};
      const sha256 = text(pin.sha256).toLowerCase();
      if (!found) {
        found = { sha256, source: record.source };
      } else if (found.sha256 !== sha256) {
        fail(`The records of ${JSON.stringify(sourceId)} cite two different PDFs.`);
      }
    }
  }
  if (!found) {
    fail(`No shard holds a record of the PDF ${JSON.stringify(sourceId)}, which overlay.config.json `
         + "names.");
  }
  if (!/^[a-f\d]{64}$/.test(found.sha256)) {
    fail(`The PDF ${JSON.stringify(sourceId)} pins no sha256, so it cannot be verified.`);
  }
  return found.source;
}

/**
 * The page → region → item → card index the viewer renders, built purely from
 * the shards (`[{url, records}]`) and the configuration.
 *
 * A record is one link: its region is its `location`, its item and region ids
 * are the extension's, and a record with a `target` adds one card to its item
 * (once per item, however many regions repeat it). `warnings` holds one line
 * per record this build could not place; such a record is dropped, never
 * guessed at.
 */
export function buildIndex(shards, config, { pageOrigin = "" } = {}) {
  const source = pdfSource(shards, config.sourceId);
  const pin = isObject(source.pin) ? source.pin : {};
  const sourceExt = extensionOf(source);
  const pageCount = Number.isInteger(sourceExt.physical_page_count) ? sourceExt.physical_page_count : null;
  const items = new Map();
  const regions = new Map();
  const seen = new Map();
  const warnings = [];
  for (const shard of asArray(shards)) {
    const context = { baseUrl: shard.url, pageOrigin, stripPrefix: config.stripPrefix ?? "" };
    for (const record of asArray(shard.records)) {
      if (!ofSource(record, config.sourceId)) {
        continue;
      }
      const ext = extensionOf(record);
      let place;
      try {
        place = recordRegion(record);
      } catch (error) {
        warnings.push(`${text(record.id) || shard.url}: ${error instanceof Error ? error.message : error}`);
        continue;
      }
      if (pageCount !== null && place.page > pageCount) {
        warnings.push(`${text(record.id)}: page ${place.page} is past the source's ${pageCount} pages.`);
        continue;
      }
      const itemId = text(ext.item) || text(record.id) || text(record.label) || `p${place.page}`;
      const regionId = text(ext.region) || `p${place.page}:${record.location.rect.join(",")}`;
      if (!items.has(itemId)) {
        items.set(itemId, {
          cards: [],
          clusterIds: asArray(ext.clusters).map(text).filter(Boolean),
          coverage: statusValue(record.status),
          coverageNote: text(record.note),
          id: itemId,
          kind: text(record.kind),
          label: text(record.label) || itemId,
          locator: locatorText(ext.locator),
          verdicts: asArray(ext.verdicts).filter(isObject),
        });
        seen.set(itemId, new Set());
      }
      const key = text(record.id) || JSON.stringify(record.target);
      if (record.target !== undefined && !seen.get(itemId).has(key)) {
        seen.get(itemId).add(key);
        items.get(itemId).cards.push(card(record, ext, context));
      }
      if (!regions.has(regionId)) {
        regions.set(regionId, { id: regionId, itemIds: [], order: [], page: place.page, quads: place.quads });
      }
      const region = regions.get(regionId);
      if (!region.itemIds.includes(itemId)) {
        region.itemIds.push(itemId);
      }
      if (!region.order.length) {
        region.order = asArray(ext.region_items).map(text).filter(Boolean);
      }
    }
  }
  for (const item of items.values()) {
    item.cards = orderCards(item.cards);
    item.roles = [...new Set(item.cards.map((entry) => entry.role))];
  }
  const pages = new Map();
  for (const region of regions.values()) {
    // The curation's order (the item the region's own label names first), not shard order.
    const rank = (id) => (region.order.includes(id) ? region.order.indexOf(id) : region.order.length);
    region.itemIds = region.itemIds
      .map((id, index) => ({ id, index }))
      .sort((left, right) => rank(left.id) - rank(right.id) || left.index - right.index)
      .map(({ id }) => id);
    delete region.order;
    region.label = region.itemIds.map((id) => items.get(id).label).join(" + ");
    if (!pages.has(region.page)) {
      pages.set(region.page, []);
    }
    pages.get(region.page).push(region);
  }
  return {
    bytes: Number.isInteger(sourceExt.bytes) ? sourceExt.bytes : null,
    items,
    pageCount,
    pages,
    regions: [...regions.values()],
    sha256: text(pin.sha256).toLowerCase(),
    source,
    sourceTitle: text(pin.title),
    warnings,
  };
}

// --- the card list ----------------------------------------------------------

export function cardsMatching(cards, filters = {}) {
  const role = text(filters.role);
  const query = text(filters.query).toLocaleLowerCase();
  return asArray(cards).filter((entry) => {
    if (role && entry.role !== role) {
      return false;
    }
    if (!query) {
      return true;
    }
    return [entry.fullName, entry.name, entry.module, entry.kind, entry.declarationId]
      .join("\n")
      .toLocaleLowerCase()
      .includes(query);
  });
}

export function paginateCards(cards, requestedPage, pageSize = CARD_PAGE_SIZE) {
  if (!Number.isInteger(pageSize) || pageSize <= 0) {
    throw new Error("The card page size must be a positive integer.");
  }
  const pageCount = Math.max(1, Math.ceil(cards.length / pageSize));
  const page = Math.min(Math.max(Number(requestedPage) || 1, 1), pageCount);
  return {
    items: cards.slice((page - 1) * pageSize, page * pageSize),
    page,
    pageCount,
    total: cards.length,
  };
}

/** The cards grouped by role, in the role order the list is already sorted by. */
export function groupCards(cards) {
  const groups = [];
  for (const entry of asArray(cards)) {
    const last = groups[groups.length - 1];
    if (last && last.role === entry.role) {
      last.cards.push(entry);
    } else {
      groups.push({ cards: [entry], role: entry.role });
    }
  }
  return groups;
}
