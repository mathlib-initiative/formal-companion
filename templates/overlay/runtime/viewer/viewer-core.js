/**
 * The overlay viewer's pure functions: document indexing, coordinate conversion,
 * the href and fragment rules, and sha256 verification.
 *
 * Nothing here touches the DOM, so `node --test` exercises all of it; viewer.js
 * holds the controller. Nothing here is specific to one companion either: every
 * per-book value arrives as data, from an `overlay.config.json`
 * (docs/overlay.md section 3) and from an annotation document, and a value this
 * module cannot read is an error with a message, never a guess.
 *
 * Nor is anything here specific to one release of the annotation format: the
 * accepted `schema` and `schema_version` are read from the schema file this
 * directory ships (`documentFormat`), never written down as a constant.
 */

// --- the two documents -----------------------------------------------------

export const CONFIG_SCHEMA = "formal-companion/overlay-config";
export const CONFIG_SCHEMA_VERSION = 1;

/**
 * The annotation format's own JSON Schema, as `cf overlay sync` copied it out
 * of the vendored release. The viewer reads which documents it accepts from
 * that file and holds no copy of the identity itself, so a format upgrade is a
 * re-sync and nothing to edit here.
 */
export const DOCUMENT_SCHEMA_URL = "../schema/annotations.schema.json";

/** The viewer never reads a JSON file larger than this. */
export const MAX_JSON_BYTES = 64 * 1024 * 1024;

/** `pdf.route` must start with this and carry no `..` (the contract's section 3). */
export const ROUTE_PREFIX = "/__";

/** Where the site root sits from `<output_root>/<mount>/viewer/index.html`. */
export const DEFAULT_SITE_ROOT = "../../";
export const DEFAULT_BACK_LINK = Object.freeze({ href: "../../index.html", text: "Back to the site" });
export const DEFAULT_STORAGE_KEY = "formal-companion-overlay-page";
export const DEFAULT_DOCUMENT_URL = "../annotations.json";
export const CONFIG_URL = "../overlay.config.json";

/** How many declaration cards one page of the card list holds. */
export const CARD_PAGE_SIZE = 25;

// --- vocabulary (the format's, copied as data the viewer can order by) -------

/** A link's `role`, in the order the card list shows them (the contract's section 4). */
export const LINK_ROLES = Object.freeze(["key", "attributed", "support", "example", "helper"]);

/** A cluster membership's `role`, in the order the fallback list shows them. */
export const MEMBERSHIP_ROLES = Object.freeze(["primary", "support", "consumer"]);

/** A verdict's `verdict`, and each `declarations[].verdict`. */
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
 * One generic line per coverage value: what the item's links do NOT show on
 * their own. The first six are the format's own values
 * (formal_companion_annotations.vocab.ANNOTATION_COVERAGE); `partial_only` and
 * `private_only` are the two further spellings docs/overlay.md
 * section 4 names, kept so a document written to the contract's wording also
 * reads. No value is ever guessed: an unknown one shows no summary.
 */
export const COVERAGE_SUMMARIES = Object.freeze({
  example_only: "Formalised only by anonymous examples; no named declaration is attributed to it.",
  formalized: "Formalised in this companion: a key declaration states it.",
  not_formalized: "Not formalised in this companion.",
  out_of_scope: "Outside the scope this companion set itself.",
  partial: "Formalised only in part: the attributed declarations state a special case or one part of it.",
  partial_only:
    "Formalised only in part: the attributed declarations state a special case or one part of it.",
  private_only: "Stated only by a private declaration.",
  support_only: "Supported only by declarations that state a neighbouring item.",
});

/** Every coverage value `COVERAGE_SUMMARIES` has a line for, sorted. */
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

/** Index a record array by its `id`; the first record of an id wins. */
function byId(records) {
  const index = new Map();
  for (const record of asArray(records)) {
    const id = isObject(record) ? text(record.id) : "";
    if (id && !index.has(id)) {
      index.set(id, record);
    }
  }
  return index;
}

/** U+0000, spelled so it is visible in the source rather than a stray byte. */
const VERDICT_KEY_SEPARATOR = String.fromCharCode(0);

/**
 * The key one `verdicts[].declarations[]` entry is indexed under. A verdict
 * belongs to an (item, declaration) pair — the same declaration under another
 * item is another judgement — and the separator occurs in no id, so the two
 * parts of a key can never run together. `buildIndex` writes the same key.
 */
function verdictKey(itemId, declarationId) {
  return [itemId, declarationId].join(VERDICT_KEY_SEPARATOR);
}

function groupBy(records, key) {
  const index = new Map();
  for (const record of asArray(records)) {
    const value = isObject(record) ? text(record[key]) : "";
    if (!value) {
      continue;
    }
    if (!index.has(value)) {
      index.set(value, []);
    }
    index.get(value).push(record);
  }
  return index;
}

// --- hashing ----------------------------------------------------------------

export function bytesToHex(bytes) {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(bytes, cryptoProvider = globalThis.crypto) {
  if (!cryptoProvider?.subtle) {
    throw new Error("WebCrypto is unavailable; the PDF cannot be verified safely.");
  }
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const digest = await cryptoProvider.subtle.digest(
    "SHA-256",
    view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength),
  );
  return bytesToHex(new Uint8Array(digest));
}

/**
 * Compare the bytes with the sha256 the document's source record pins.
 * The expected value is a parameter: no hash of any one PDF lives in a template.
 */
export async function verifyPdfBytes(bytes, expected, cryptoProvider = globalThis.crypto) {
  const want = text(expected).toLowerCase();
  if (!/^[a-f\d]{64}$/.test(want)) {
    throw new Error("The source record's sha256 is not 64 hex digits; the PDF cannot be verified.");
  }
  const actual = await sha256Hex(bytes, cryptoProvider);
  return { actual, expected: want, ok: actual === want };
}

// --- hrefs and fragments ----------------------------------------------------

const SCHEME = /^[a-z][a-z\d+.-]*:/i;

/**
 * The grammar a destination's relative `href` follows (format section 3.6, the
 * same grammar as `SitePageSelector.path`): a path relative to the site root,
 * with no leading `/`, no `..` segment, and no whitespace, `#` or `:` (so it
 * can never read as a URL scheme). Returns the path, or null.
 */
export function safeSitePath(value) {
  const path = typeof value === "string" ? value : "";
  if (!path || /[\s#:]/.test(path) || path.includes("\\")) {
    return null;
  }
  if (path.startsWith("/") || /(^|\/)\.\.(\/|$)/.test(path)) {
    return null;
  }
  return path;
}

/** An `https:` URL with no fragment of its own, or null. */
export function safeHttpsUrl(value) {
  const url = typeof value === "string" ? value : "";
  return /^https:\/\/[^\s#\\]+$/.test(url) ? url : null;
}

/** A fragment, percent-encoded, so no document string can escape the URL. */
export function encodeFragment(fragment) {
  const value = typeof fragment === "string" ? fragment : "";
  return value ? encodeURIComponent(value) : "";
}

/**
 * The back link's own `href`, or null. It is the one href the config supplies
 * rather than the document, and its default (`../../index.html`) climbs out of
 * the mount, so the destination rule of the contract's section 4 cannot apply verbatim:
 * a relative path is accepted with its `..` segments, and an `https:` URL is
 * accepted whole, but a scheme-bearing (`javascript:`, `data:`), protocol-
 * relative or root-absolute value is refused so the page header can never
 * carry a live link the config smuggled in.
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
 * The directory a destination's relative `href` resolves against: the back
 * link's own directory. A back link that is absolute or carries a scheme is not
 * a relative path at all, so the default mount depth is used instead.
 */
export function siteRootFrom(href) {
  const value = text(href);
  if (!value || value.startsWith("/") || value.includes("\\") || SCHEME.test(value)) {
    return DEFAULT_SITE_ROOT;
  }
  const cut = value.lastIndexOf("/");
  return cut < 0 ? "" : value.slice(0, cut + 1);
}

/**
 * The href a destination record yields, or null when the record names nothing
 * this viewer is willing to link to. An `exact` destination appends its
 * percent-encoded `fragment`; a `fallback` one does not.
 */
export function destinationHref(destination, root = DEFAULT_SITE_ROOT) {
  if (!isObject(destination)) {
    return null;
  }
  const absolute = safeHttpsUrl(destination.href);
  const relative = absolute ? null : safeSitePath(destination.href);
  if (!absolute && !relative) {
    return null;
  }
  const base = absolute ?? `${root}${relative}`;
  if (destination.resolution !== "exact") {
    return base;
  }
  const fragment = encodeFragment(destination.fragment);
  return fragment ? `${base}#${fragment}` : base;
}

// --- the configuration ------------------------------------------------------

/**
 * Read `overlay.config.json`: check what the viewer relies on, and fill in the
 * defaults of docs/overlay.md section 3 for everything else. The full
 * rule set is `tools/check_generated.py`'s; this is the subset the page needs
 * before it will fetch anything.
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
  // An href the back-link rule refuses falls back to the default, which is the
  // site index at the depth the mount sits at.
  const backHref = safeBackLinkHref(backLink.href) ?? DEFAULT_BACK_LINK.href;
  const display = isObject(raw.display) ? raw.display : {};
  return {
    backLink: {
      href: backHref,
      text: text(backLink.text) || DEFAULT_BACK_LINK.text,
    },
    // Section 3's key table has no entry for the document's path: there is one
    // place the generated document sits, and the viewer reads only that one.
    documentUrl: DEFAULT_DOCUMENT_URL,
    lede: text(raw.lede),
    pdfRoute: route,
    siteRoot: siteRootFrom(backHref),
    sourceId,
    storageKey: text(raw.storage_key) || DEFAULT_STORAGE_KEY,
    stripPrefix: text(display.strip_prefix),
    title,
  };
}

// --- the annotation format --------------------------------------------------

/**
 * Which documents the shipped annotation schema accepts: `{schema, version}`,
 * the schema's `properties.schema.const` and a `RegExp` anchored on its
 * `properties.schema_version.pattern`.
 *
 * A schema file that states neither is refused with a message rather than read
 * as "accept anything": the viewer has no second copy of the identity, and a
 * guess here would open a document the companion's own validator refuses.
 */
export function documentFormat(schemaJson) {
  if (!isObject(schemaJson)) {
    fail(`${DOCUMENT_SCHEMA_URL} must be a JSON Schema object.`);
  }
  const properties = isObject(schemaJson.properties) ? schemaJson.properties : {};
  const schema = isObject(properties.schema) ? properties.schema : {};
  const identity = text(schema.const);
  if (!identity) {
    fail(`${DOCUMENT_SCHEMA_URL} states no properties.schema.const, so the accepted `
         + "document identity cannot be read. Re-run `cf overlay sync`.");
  }
  const versions = isObject(properties.schema_version) ? properties.schema_version : {};
  const pattern = text(versions.pattern);
  if (!pattern) {
    fail(`${DOCUMENT_SCHEMA_URL} states no properties.schema_version.pattern, so the `
         + "accepted document versions cannot be read. Re-run `cf overlay sync`.");
  }
  let version;
  try {
    version = new RegExp(pattern);
  } catch (error) {
    fail(`${DOCUMENT_SCHEMA_URL} has a properties.schema_version.pattern that is not a `
         + `regular expression (${error instanceof Error ? error.message : error}).`);
  }
  return { schema: identity, version };
}

/** Does *value* match the format's `schema_version` pattern end to end? */
function matchesVersion(format, value) {
  if (typeof value !== "string") {
    return false;
  }
  const found = format.version.exec(value);
  return Boolean(found) && found[0] === value;
}

// --- the annotation document ------------------------------------------------

/**
 * The `kind: pdf` source the config names, after the three refusals of
 * docs/overlay.md section 4: the document's schema, its version, and a source
 * that carries `sha256` and `physical_page_count`. The first two are the
 * shipped schema's, through *format* (`documentFormat`).
 */
export function pdfSource(doc, sourceId, format) {
  if (!isObject(doc)) {
    fail("The annotation document must be a JSON object.");
  }
  if (!isObject(format) || typeof format.schema !== "string" || !(format.version instanceof RegExp)) {
    fail("The annotation format was not read from "
         + `${DOCUMENT_SCHEMA_URL}; the viewer cannot judge this document.`);
  }
  if (doc.schema !== format.schema) {
    fail(`The annotation document has schema ${JSON.stringify(doc.schema)}; expected `
         + `${JSON.stringify(format.schema)}.`);
  }
  if (!matchesVersion(format, doc.schema_version)) {
    fail(`The annotation document is schema_version ${JSON.stringify(doc.schema_version)}; this `
         + `overlay ships a schema for ${format.version.source}.`);
  }
  const source = asArray(doc.sources).find((entry) => isObject(entry) && entry.id === sourceId);
  if (!source) {
    fail(`The annotation document has no source ${JSON.stringify(sourceId)}, which `
         + "overlay.config.json names.");
  }
  if (source.kind !== "pdf") {
    fail(`Source ${JSON.stringify(sourceId)} is kind ${JSON.stringify(source.kind)}; the overlay `
         + "needs a pdf source.");
  }
  if (!/^[a-f\d]{64}$/.test(text(source.sha256))) {
    fail(`Source ${JSON.stringify(sourceId)} carries no sha256, so its PDF cannot be verified.`);
  }
  if (!Number.isInteger(source.physical_page_count) || source.physical_page_count < 1) {
    fail(`Source ${JSON.stringify(sourceId)} carries no physical_page_count.`);
  }
  return source;
}

// --- geometry (the format's PdfRegionSelector, format section 4.2) ----------

/** The size of one physical page: its `page_sizes` range, else `page_size`. */
export function pageSizeFor(source, page) {
  for (const range of asArray(source?.page_sizes)) {
    if (isObject(range) && range.first <= page && page <= range.last) {
      return range;
    }
  }
  return isObject(source?.page_size) ? source.page_size : null;
}

/**
 * The quadrilaterals one `PdfRegionSelector` covers, as fractions of the page
 * with the origin at the top left — whatever `units` (`normalized` or `pt`) and
 * `origin` (`top_left` or `bottom_left`) the selector declares. `pt` needs the
 * source's `page_size` or a matching `page_sizes` range; without one the region
 * cannot be placed and this throws rather than guessing a page size.
 */
export function regionQuads(selector, source) {
  if (!isObject(selector) || selector.type !== "PdfRegionSelector") {
    fail("A region is a PdfRegionSelector.");
  }
  const page = selector.page;
  if (!Number.isInteger(page) || page < 1) {
    fail("A PdfRegionSelector names a 1-based physical page.");
  }
  const space = selector.coordinate_space;
  if (!isObject(space)) {
    fail(`The region on page ${page} declares no coordinate_space.`);
  }
  if (space.origin !== "top_left" && space.origin !== "bottom_left") {
    fail(`The region on page ${page} has an unknown coordinate origin `
         + `${JSON.stringify(space.origin)}.`);
  }
  let width = 1;
  let height = 1;
  if (space.units === "pt") {
    const size = pageSizeFor(source, page);
    if (!size || !(size.width_pt > 0) || !(size.height_pt > 0)) {
      fail(`The region on page ${page} is in pt, and the source gives no page size for that page.`);
    }
    width = size.width_pt;
    height = size.height_pt;
  } else if (space.units !== "normalized") {
    fail(`The region on page ${page} has unknown coordinate units `
         + `${JSON.stringify(space.units)}.`);
  }
  const flip = space.origin === "bottom_left";
  const point = (x, y) => {
    const fx = Number(x) / width;
    const fy = Number(y) / height;
    if (!Number.isFinite(fx) || !Number.isFinite(fy)) {
      fail(`The region on page ${page} has non-finite coordinates.`);
    }
    const top = flip ? 1 - fy : fy;
    if (fx < 0 || fx > 1 || top < 0 || top > 1) {
      fail(`The region on page ${page} leaves the page.`);
    }
    return [fx, top];
  };

  if (isObject(selector.rect)) {
    const { rect } = selector;
    const [ax, ay] = point(rect.x0, rect.y0);
    const [bx, by] = point(rect.x1, rect.y1);
    const x0 = Math.min(ax, bx);
    const x1 = Math.max(ax, bx);
    const y0 = Math.min(ay, by);
    const y1 = Math.max(ay, by);
    if (x0 === x1 || y0 === y1) {
      fail(`The region on page ${page} has no area.`);
    }
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]];
  }
  if (Array.isArray(selector.quads) && selector.quads.length) {
    return selector.quads.map((quad) => {
      if (!isObject(quad)) {
        fail(`The region on page ${page} has a malformed quad.`);
      }
      const corners = [0, 1, 2, 3].map((n) => point(quad[`x${n}`], quad[`y${n}`]));
      const distinct = new Set(corners.map(([x, y]) => `${x},${y}`));
      if (distinct.size < 3) {
        fail(`The region on page ${page} has a quad with fewer than three distinct corners.`);
      }
      return corners;
    });
  }
  fail(`The region on page ${page} carries neither a rect nor quads.`);
  return [];
}

/** A point in page fractions (top-left origin) placed in a PDF.js viewport. */
export function normalizedPointToViewport(point, pageView, viewport) {
  const [x, y] = Array.isArray(point) ? point.map(Number) : [NaN, NaN];
  if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || x > 1 || y < 0 || y > 1) {
    throw new Error("Overlay coordinates must be finite page fractions in [0, 1].");
  }
  if (!Array.isArray(pageView) || pageView.length !== 4) {
    throw new Error("A PDF page view box must have four coordinates.");
  }
  const [xMin, yMin, xMax, yMax] = pageView.map(Number);
  if (![xMin, yMin, xMax, yMax].every(Number.isFinite) || xMax <= xMin || yMax <= yMin) {
    throw new Error("The PDF page view box is invalid.");
  }
  if (typeof viewport?.convertToViewportPoint !== "function") {
    throw new Error("A PDF.js viewport is required for overlay transformation.");
  }
  return viewport.convertToViewportPoint(xMin + x * (xMax - xMin), yMax - y * (yMax - yMin));
}

export function quadToViewport(quad, pageView, viewport) {
  if (!Array.isArray(quad) || quad.length !== 4) {
    throw new Error("An overlay quadrilateral must contain four points.");
  }
  return quad.map((point) => normalizedPointToViewport(point, pageView, viewport));
}

/**
 * Every `PdfRegionSelector` one `target.selector` alternative reaches, through
 * `refinedBy` and through either end of a `RangeSelector`. Alternatives select
 * the same span, so a caller takes the first one that yields any.
 */
export function regionSelectors(alternative) {
  const found = [];
  const walk = (node) => {
    if (!isObject(node)) {
      return;
    }
    if (node.type === "PdfRegionSelector") {
      found.push(node);
    }
    walk(node.refinedBy);
    walk(node.startSelector);
    walk(node.endSelector);
  };
  walk(alternative);
  return found;
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

/** The generic one-line summary of a coverage value, or "" for an unknown one. */
export function coverageSummary(coverage) {
  return COVERAGE_SUMMARIES[coverage] ?? "";
}

/**
 * The line above the card list. A coverage value speaks for the item; otherwise
 * the line counts what is shown, and says plainly when the rows come from the
 * item's clusters rather than from links of its own.
 */
export function cardSummaryText({ clusters = 0, coverage = null, fallback = false, shown = 0 }) {
  const summary = coverage ? coverageSummary(coverage) : "";
  if (summary) {
    return summary;
  }
  if (fallback) {
    return `No declaration is linked to this item; showing ${plural(shown, "declaration")} `
      + `from ${plural(clusters, "cluster")} it belongs to.`;
  }
  return `${plural(shown, "linked declaration")}.`;
}

// --- the index --------------------------------------------------------------

function itemLabel(item) {
  const label = text(item.label);
  if (label) {
    return label;
  }
  if (item.form === "numbered") {
    const sub = text(item.sub);
    return `${text(item.kind)} ${text(item.number)}${sub ? `(${sub})` : ""}`.trim();
  }
  return text(item.title) || text(item.id);
}

function itemLocator(item) {
  const parts = [];
  const section = text(item.section);
  if (section) {
    parts.push(section);
  }
  if (isObject(item.pages)) {
    const { first } = item.pages;
    const last = item.pages.last ?? first;
    const span = first === last ? `p. ${first}` : `pp. ${first}-${last}`;
    parts.push(`${span} (${text(item.pages.basis) || "physical"})`);
  }
  return parts.join(" · ");
}

function stripPrefix(name, prefix) {
  return prefix && name.startsWith(prefix) ? name.slice(prefix.length) : name;
}

function declarationCard(declaration, context, extra) {
  const id = text(declaration?.id) || text(extra.declarationId);
  const fullName = text(declaration?.name) || id;
  const destination = context.destinations.get(id) ?? null;
  const href = destinationHref(destination, context.siteRoot);
  const badges = [];
  if (declaration?.private) {
    badges.push("private");
  }
  if (declaration?.anonymous) {
    badges.push("anonymous");
  }
  if (declaration?.sorry && declaration.sorry !== "none") {
    badges.push(`sorry: ${declaration.sorry}`);
  }
  if (destination?.resolution === "fallback") {
    badges.push("fallback");
  }
  if (!href) {
    badges.push("unlinked");
  }
  const snippet = context.snippets.get(id) ?? null;
  return {
    badges,
    declarationId: id,
    fallbackLabel: destination?.resolution === "fallback" ? text(destination.fallback_label) : "",
    fullName,
    href,
    kind: text(declaration?.kind),
    module: text(declaration?.module),
    name: stripPrefix(fullName, context.stripPrefix) || fullName,
    resolution: text(destination?.resolution),
    snippet: snippet ? { line: snippet.line ?? null, text: text(snippet.text) } : null,
    verdict: context.verdictsByDeclaration.get(verdictKey(extra.itemId, id)) ?? null,
    ...extra,
  };
}

function clusterCard(cluster, context, extra) {
  return {
    badges: ["cluster"],
    cluster: { id: text(cluster?.id), locator: text(cluster?.locator), title: text(cluster?.title) },
    declarationId: "",
    fallbackLabel: "",
    fullName: text(cluster?.title) || text(cluster?.id),
    href: null,
    kind: "",
    module: "",
    name: text(cluster?.title) || text(cluster?.id),
    resolution: "",
    snippet: null,
    verdict: null,
    ...extra,
  };
}

function orderCards(cards, order) {
  return cards
    .map((card, index) => ({ card, index }))
    .sort((left, right) => {
      const a = order.indexOf(left.card.role);
      const b = order.indexOf(right.card.role);
      const rank = (a < 0 ? order.length : a) - (b < 0 ? order.length : b);
      return rank !== 0 ? rank : left.index - right.index;
    })
    .map((entry) => entry.card);
}

function buildItem(item, context) {
  const id = text(item.id);
  const links = (context.linksByItem.get(id) ?? []).filter((link) => !link.orphaned);
  const cards = orderCards(
    links.map((link) => {
      const shared = {
        fallback: false,
        id: text(link.id),
        itemId: id,
        note: text(link.note),
        relation: text(link.relation),
        role: text(link.role),
      };
      if (text(link.declaration)) {
        return declarationCard(context.declarations.get(text(link.declaration)), context, {
          ...shared,
          declarationId: text(link.declaration),
        });
      }
      return clusterCard(context.clusters.get(text(link.cluster)), context, shared);
    }),
    LINK_ROLES,
  );

  // The item's `cluster_fallback` case (format section 3.2): an item with no
  // links of its own but with clusters is shown through those clusters'
  // memberships, labelled as a fallback.
  const clusterIds = asArray(item.clusters).map(text).filter(Boolean);
  const fallbackCards = cards.length === 0
    ? orderCards(
      clusterIds.flatMap((clusterId) =>
        (context.membershipsByCluster.get(clusterId) ?? [])
          .filter((member) => text(member.declaration))
          .map((member) => declarationCard(
            context.declarations.get(text(member.declaration)),
            context,
            {
              cluster: {
                id: clusterId,
                locator: text(context.clusters.get(clusterId)?.locator),
                title: text(context.clusters.get(clusterId)?.title),
              },
              declarationId: text(member.declaration),
              fallback: true,
              id: text(member.id),
              itemId: id,
              note: "",
              relation: "",
              role: text(member.role),
            },
          ))),
      MEMBERSHIP_ROLES,
    )
    : [];
  const usesFallback = fallbackCards.length > 0;

  return {
    cards: usesFallback ? fallbackCards : cards,
    clusterIds,
    clusterTitles: clusterIds.map((clusterId) =>
      text(context.clusters.get(clusterId)?.title) || clusterId),
    coverage: text(item.coverage),
    // The format carries a producer's note under the `x_` extension prefix;
    // docs/overlay.md section 4 spells it without one, so both read.
    coverageNote: text(item.coverage_note) || text(item.x_coverage_note),
    id,
    kind: text(item.kind),
    label: itemLabel(item),
    locator: itemLocator(item),
    roles: [...new Set((usesFallback ? fallbackCards : cards).map((card) => card.role))],
    usesFallback,
    verdicts: context.verdictsByItem.get(id) ?? [],
  };
}

/**
 * The page → region → item → card index the viewer renders, built purely from
 * the annotation document and the configuration.
 *
 * `warnings` holds one line per location this build could not place (a region
 * in `pt` on a page with no declared size, coordinates off the page); such a
 * location is dropped, never guessed at.
 */
export function buildIndex(doc, config, format) {
  const source = pdfSource(doc, config.sourceId, format);
  const context = {
    clusters: byId(doc.clusters),
    declarations: byId(doc.declarations),
    destinations: new Map(),
    linksByItem: groupBy(doc.links, "item"),
    membershipsByCluster: groupBy(doc.memberships, "cluster"),
    siteRoot: config.siteRoot ?? DEFAULT_SITE_ROOT,
    snippets: new Map(),
    stripPrefix: config.stripPrefix ?? "",
    verdictsByDeclaration: new Map(),
    verdictsByItem: new Map(),
  };
  for (const snippet of asArray(doc.snippets)) {
    const declaration = text(snippet?.declaration);
    if (declaration && !context.snippets.has(declaration)) {
      context.snippets.set(declaration, snippet);
    }
  }
  for (const destination of asArray(doc.destinations)) {
    const declaration = text(destination?.declaration);
    if (declaration && !context.destinations.has(declaration)) {
      context.destinations.set(declaration, destination);
    }
  }
  for (const verdict of asArray(doc.verdicts)) {
    const itemId = text(verdict?.item);
    if (!itemId) {
      continue;
    }
    if (!context.verdictsByItem.has(itemId)) {
      context.verdictsByItem.set(itemId, []);
    }
    context.verdictsByItem.get(itemId).push(verdict);
    for (const entry of asArray(verdict.declarations)) {
      const key = verdictKey(itemId, text(entry?.declaration));
      if (text(entry?.declaration) && !context.verdictsByDeclaration.has(key)) {
        context.verdictsByDeclaration.set(key, {
          declaration: text(entry.declaration),
          note: text(entry.note),
          status: text(verdict.status),
          verdict: text(entry.verdict),
        });
      }
    }
  }

  const items = new Map();
  for (const item of asArray(doc.items)) {
    if (isObject(item) && item.source === config.sourceId && text(item.id)) {
      items.set(text(item.id), buildItem(item, context));
    }
  }

  const regions = [];
  const warnings = [];
  for (const location of asArray(doc.locations)) {
    if (!isObject(location) || location.target?.source !== config.sourceId) {
      continue;
    }
    let selectors = [];
    for (const alternative of asArray(location.target?.selector)) {
      selectors = regionSelectors(alternative);
      if (selectors.length) {
        break;
      }
    }
    if (!selectors.length) {
      continue;
    }
    const shown = asArray(location.items).map(text).filter((id) => items.has(id));
    selectors.forEach((selector, index) => {
      let quads;
      try {
        quads = regionQuads(selector, source);
      } catch (error) {
        warnings.push(`${text(location.id)}: ${error instanceof Error ? error.message : error}`);
        return;
      }
      if (selector.page > source.physical_page_count) {
        warnings.push(`${text(location.id)}: page ${selector.page} is past the source's `
                      + `${source.physical_page_count} physical pages.`);
        return;
      }
      regions.push({
        id: selectors.length === 1 ? text(location.id) : `${text(location.id)}#${index}`,
        itemIds: shown,
        label: shown.map((id) => items.get(id).label).join(" + ") || text(location.id),
        locationId: text(location.id),
        page: selector.page,
        quads,
      });
    });
  }

  const pages = new Map();
  for (const region of regions) {
    if (!pages.has(region.page)) {
      pages.set(region.page, []);
    }
    pages.get(region.page).push(region);
  }

  return {
    items,
    pageCount: source.physical_page_count,
    pages,
    regions,
    sha256: text(source.sha256).toLowerCase(),
    source,
    sourceTitle: text(source.title),
    warnings,
  };
}

// --- the card list ----------------------------------------------------------

export function cardsMatching(cards, filters = {}) {
  const role = text(filters.role);
  const query = text(filters.query).toLocaleLowerCase();
  return asArray(cards).filter((card) => {
    if (role && card.role !== role) {
      return false;
    }
    if (!query) {
      return true;
    }
    return [card.fullName, card.name, card.module, card.kind, card.declarationId]
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
  for (const card of asArray(cards)) {
    const last = groups[groups.length - 1];
    if (last && last.role === card.role) {
      last.cards.push(card);
    } else {
      groups.push({ cards: [card], role: card.role });
    }
  }
  return groups;
}
