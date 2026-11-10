/**
 * Whole-PDF literal search, new code implementing docs/overlay.md section 4.
 * Offsets refer to the unmodified PDF.js text layer; no text leaves the viewer.
 */
export function searchablePage(items) {
  const units = [];
  let offset = 0;
  for (const item of items) {
    if (typeof item.str !== "string") continue;
    for (const char of item.str) {
      units.push({ char, start: offset, end: offset + char.length });
      offset += char.length;
    }
    if (item.hasEOL) units.push({ char: "\n", start: offset, end: offset });
  }
  let text = "";
  const starts = [], ends = [];
  for (let i = 0; i < units.length; i++) {
    const unit = units[i];
    if (/[\u00ad\u200b]/u.test(unit.char)) continue;
    // A printed line-end hyphen is not part of the word being searched.
    if (/[-‐]/u.test(unit.char) && /\p{L}/u.test(units[i - 1]?.char ?? "")) {
      let j = i + 1, newline = false;
      while (j < units.length && /\s/u.test(units[j].char)) {
        newline ||= units[j].char === "\n";
        j++;
      }
      if (newline && /\p{Ll}/u.test(units[j]?.char ?? "")) { i = j - 1; continue; }
    }
    let value = unit.char.normalize("NFKC").toLowerCase();
    if (/^\s+$/u.test(value)) {
      if (text.endsWith(" ")) { ends[ends.length - 1] = unit.end; continue; }
      value = " ";
    }
    text += value;
    for (let j = 0; j < value.length; j++) { starts.push(unit.start); ends.push(unit.end); }
  }
  return { text, starts, ends };
}

export function findPageMatches(page, query) {
  const needle = searchablePage([{ str: query }]).text.trim();
  if (!needle) return [];
  const matches = [];
  for (let at = page.text.indexOf(needle); at !== -1; at = page.text.indexOf(needle, at + needle.length)) {
    matches.push({ start: page.starts[at], end: page.ends[at + needle.length - 1] });
  }
  return matches;
}

/** Extract on demand, once per page; searches superseded by typing stop between awaits. */
export function createPdfTextIndex(pdf) {
  const pages = new Map();
  async function pageText(number) {
    if (!pages.has(number)) {
      const pending = pdf.getPage(number)
        .then((page) => page.getTextContent({ disableNormalization: true }))
        .then(({ items }) => searchablePage(items));
      pages.set(number, pending);
      pending.catch(() => pages.delete(number));
    }
    return pages.get(number);
  }
  return {
    async search(query, { cancelled = () => false, onProgress = () => {} } = {}) {
      const matches = [], failedPages = [];
      let textPages = 0;
      for (let number = 1; number <= pdf.numPages; number++) {
        if (cancelled()) return null;
        try {
          const page = await pageText(number);
          if (cancelled()) return null;
          if (page.text.trim()) textPages++;
          for (const match of findPageMatches(page, query)) matches.push({ page: number, ...match });
        } catch {
          if (cancelled()) return null;
          failedPages.push(number);
        }
        onProgress(number);
      }
      return { matches, failedPages, textPages };
    },
  };
}

/** Use PDF.js's own text divs: no rewritten spans, so copying and selection stay intact. */
export function searchTextRange(documentRef, layer, { start, end }) {
  if (!documentRef.createRange || !layer?.textDivs) return null;
  let offset = 0, first = null, last = null;
  for (let i = 0; i < layer.textDivs.length; i++) {
    const length = layer.textContentItemsStr[i].length;
    const node = layer.textDivs[i].firstChild;
    if (node && !first && start >= offset && start < offset + length) first = [node, start - offset];
    if (node && end > offset && end <= offset + length) { last = [node, end - offset]; break; }
    offset += length;
  }
  if (!first || !last) return null;
  const range = documentRef.createRange();
  range.setStart(...first);
  range.setEnd(...last);
  return range;
}
