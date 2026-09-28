"""Write a small public-domain booklet as a PDF, with the stdlib only.

This is the SECOND source of the multi-source demonstration.  It exists so the
model is exercised against a source that is not Tao's book: a different work,
different pagination, its own item numbering, discovered and prepared by the
same machinery with nothing typed in but a title.

Shape, chosen so the inference has something real to find:
  * a title page and a contents page with NO running head, then 14 printed
    pages that do carry one -- so the page offset (printed = PDF - 2) is
    inferable from the running heads by majority, exactly as it is for Tao.
  * 28 numbered items in four sections of one chapter, in the c.s.n grammar
    the skill parses, spread over the kinds it knows.
  * with ``--passages`` (``build(passages=True)``), six PLANTED unnumbered
    passages after four of the items: five the text marks itself (``Proof.``,
    ``Remark.``, ``Example (...).``, ``Claim.``, ``Proof of ...``) and one it
    does not, so a passage finder's recall can be measured outside Tao
    (docs/proposals/pdf-to-companion.md section 2.2).  The default booklet is
    unchanged byte for byte: its sha is pinned.

usage: python3 make_pdf.py <out.pdf> [--passages]
"""
import sys
import zlib

# (kind, number, title, body) -- section by section, in printed order.
ITEMS = [
    ("Definition", "1.1.1", "Box", "A box in R^d is a product of d bounded intervals."),
    ("Definition", "1.1.2", "Elementary set",
     "An elementary set is a finite union of boxes."),
    ("Lemma", "1.1.3", "Boxes are elementary", "Every box is an elementary set."),
    ("Lemma", "1.1.4", "Boolean closure",
     "If E and F are elementary, so are E u F, E n F and E \\ F."),
    ("Exercise", "1.1.5", "", "Show that a finite union of boxes is elementary."),
    ("Exercise", "1.1.6", "", "Show that the elementary sets form a Boolean algebra."),
    ("Remark", "1.1.7", "", "Countable unions of boxes need not be elementary."),
    ("Definition", "1.2.1", "Elementary measure",
     "For an elementary set E, m(E) is the total length of a partition of E into boxes."),
    ("Lemma", "1.2.2", "Well-definedness",
     "The quantity m(E) does not depend on the chosen partition."),
    ("Theorem", "1.2.3", "Finite additivity",
     "If E and F are disjoint elementary sets then m(E u F) = m(E) + m(F)."),
    ("Corollary", "1.2.4", "Monotonicity",
     "If E is contained in F then m(E) <= m(F)."),
    ("Corollary", "1.2.5", "Subadditivity",
     "For elementary E and F, m(E u F) <= m(E) + m(F)."),
    ("Exercise", "1.2.6", "", "Prove that m is translation invariant."),
    ("Exercise", "1.2.7", "", "Prove that m(E) = 0 when E is a single point."),
    ("Example", "1.2.8", "", "The unit square has elementary measure one."),
    ("Definition", "1.3.1", "Jordan inner measure",
     "The Jordan inner measure of a bounded set A is the supremum of m(E) over elementary E inside A."),
    ("Definition", "1.3.2", "Jordan outer measure",
     "The Jordan outer measure of A is the infimum of m(F) over elementary F containing A."),
    ("Definition", "1.3.3", "Jordan measurable",
     "A bounded set is Jordan measurable when its inner and outer measures agree."),
    ("Proposition", "1.3.4", "Characterisation",
     "A bounded set is Jordan measurable if and only if its boundary has outer measure zero."),
    ("Theorem", "1.3.5", "Additivity",
     "Jordan measure is finitely additive on disjoint Jordan measurable sets."),
    ("Exercise", "1.3.6", "", "Show that every elementary set is Jordan measurable."),
    ("Exercise", "1.3.7", "", "Exhibit a bounded set that is not Jordan measurable."),
    ("Remark", "1.3.8", "", "The rationals in the unit interval are the standard example."),
    ("Definition", "1.4.1", "Lebesgue outer measure",
     "The Lebesgue outer measure of A is the infimum of the total volume of countable box covers."),
    ("Lemma", "1.4.2", "Countable subadditivity",
     "Lebesgue outer measure is countably subadditive."),
    ("Theorem", "1.4.3", "Comparison",
     "Lebesgue outer measure is at most Jordan outer measure."),
    ("Exercise", "1.4.4", "", "Show that a countable set has Lebesgue outer measure zero."),
    ("Example", "1.4.5", "", "The Cantor set is uncountable with outer measure zero."),
]

#: planted unnumbered passages: item number -> the passages set after that item.
#: ``marked`` says whether the text marks the passage with a word of its own.
PASSAGES = {
    "1.1.4": [("Proof. Write E and F as finite unions of boxes and intersect them "
               "pairwise; the pieces are boxes.", True)],
    "1.2.3": [("Remark. The same argument gives additivity for any finite family of "
               "pairwise disjoint elementary sets.", True),
              ("In particular the empty set has elementary measure zero, since it is "
               "disjoint from itself.", False)],
    "1.3.3": [("Example (Unit interval). The closed unit interval is Jordan measurable "
               "and its Jordan measure is one.", True),
              ("Claim. Every Jordan measurable set is bounded, because its outer measure "
               "is finite.", True)],
    "1.4.3": [("Proof of Theorem 1.4.3. Every finite cover by boxes is also a countable "
               "cover by boxes.", True)],
}

SECTION_TITLES = {
    "1.1": "1.1 Boxes and elementary sets",
    "1.2": "1.2 Elementary measure",
    "1.3": "1.3 Jordan measure",
    "1.4": "1.4 Lebesgue outer measure",
}
LINES_PER_PAGE = 9


def wrap(text, width=82):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def body_pages(passages=False):
    """The printed pages: a list of line-lists, each ending in its page number."""
    blocks, last_section = [], None
    for kind, number, title, body in ITEMS:
        section = number.rsplit(".", 1)[0]
        block = []
        if section != last_section:
            block += ["", SECTION_TITLES[section], ""]
            last_section = section
        head = "%s %s%s." % (kind, number, (" (%s)" % title) if title else "")
        block += wrap(head + " " + body)
        block.append("")
        blocks.append(block)
        for text, _marked in (PASSAGES.get(number, []) if passages else []):
            blocks.append(wrap(text) + [""])

    pages, cur = [], []
    for block in blocks:
        if cur and len(cur) + len(block) > LINES_PER_PAGE:
            pages.append(cur)
            cur = []
        cur += block
    if cur:
        pages.append(cur)
    # pad each page out and stamp the running head (the printed page number)
    out = []
    for i, lines in enumerate(pages, start=1):
        padded = lines + [""] * max(0, LINES_PER_PAGE - len(lines))
        out.append(padded + ["", "%s%d" % (" " * 40, i)])
    return out


FRONT = [
    ["Elementary Notes on Sets", "", "A short public-domain booklet.", "",
     "Anonymous", "", "This text is dedicated to the public domain."],
    ["Contents", "", "Chapter 1. Elementary and Jordan measure", ""]
    + ["    " + t for t in SECTION_TITLES.values()],
]
PAGES = FRONT + body_pages()


def pages(passages=False):
    """Every PDF page's lines; ``passages`` adds the planted passages."""
    return FRONT + body_pages(passages) if passages else PAGES


def escape(s):
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def content(lines):
    out = ["BT", "/F1 10 Tf", "13 TL", "56 750 Td"]
    for line in lines:
        out.append("(%s) Tj T*" % escape(line))
    out.append("ET")
    return "\n".join(out).encode("latin-1")


def build(passages=False):
    all_pages = pages(passages)
    objs = {}
    n_pages = len(all_pages)
    kids = " ".join("%d 0 R" % (4 + 2 * i) for i in range(n_pages))
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = ("<< /Type /Pages /Count %d /Kids [%s] >>" % (n_pages, kids)).encode()
    objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"
    for i, lines in enumerate(all_pages):
        page_no, stream_no = 4 + 2 * i, 5 + 2 * i
        objs[page_no] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % stream_no
        ).encode()
        data = zlib.compress(content(lines))
        objs[stream_no] = (b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data)
                           + data + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    start = len(out)
    top = max(objs) + 1
    out += b"xref\n0 %d\n" % top
    out += b"0000000000 65535 f \n"
    for num in range(1, top):
        out += b"%010d 00000 n \n" % offsets[num]
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (top, start))
    return bytes(out)


if __name__ == "__main__":
    planted = "--passages" in sys.argv[2:]
    open(sys.argv[1], "wb").write(build(planted))
    n = len(pages(planted))
    print("%s: %d PDF pages, %d printed pages, %d items%s"
          % (sys.argv[1], n, n - 2, len(ITEMS),
             ", %d planted passages" % sum(len(v) for v in PASSAGES.values())
             if planted else ""))
