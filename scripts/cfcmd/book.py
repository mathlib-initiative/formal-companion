"""``cf book <id>`` -- prepare one book directory for a campaign.

    python3 scripts/cf.py book tao-measure --pdf ~/…/gsm-126-tao5-measure-book.pdf \
        --offset 16 --errata-url https://terrytao.wordpress.com/books/… --images

Writes ``<campaign>/book/<id>/{book.txt, pages/, book.json, items.json,
errata.*}`` and stamps the edition into every companion's ``state.json``.
Idempotent: an unchanged PDF re-uses the existing text and items.

Ported from: recovered/b174aa13_L5589_Bash.txt and b174aa13_L6099_Bash.txt (the
``pdftotext -layout`` one-liners) -- see scripts/book/*.py for the details.
"""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):  # direct execution: python3 scripts/cfcmd/book.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from book import fetch_errata, prepare_book, render_pages
from book.compat import campaign_paths, load_campaign, read_json, state_merge, utcnow

NAME = "book"
HELP = "prepare a book directory (text, page map, items, errata, page images)"


def register(subparsers):
    """cf.py discovery hook."""
    p = subparsers.add_parser(NAME, help=HELP, description=__doc__)
    p.add_argument("book_id", nargs="?", help="book id (default: campaign.yaml book.id)")
    p.add_argument("--campaign", help="campaign directory (default: $CF_CAMPAIGN or cwd upward)")
    p.add_argument("--out", help="write the book dir here instead of <campaign>/book/<id>")
    p.add_argument("--pdf", help="path to the edition PDF (never copied into the skill repo)")
    p.add_argument("--offset", type=int, help="pdf_page - printed_page (Tao: 16)")
    p.add_argument("--edition", default="", help="human label for the edition")
    p.add_argument("--kinds", help="comma-separated item kinds (default: campaign.yaml or Tao's 8)")
    p.add_argument("--errata-url", help="live errata page (fetched raw with curl, never summarised)")
    p.add_argument("--no-errata", action="store_true", help="skip the errata fetch")
    p.add_argument("--images", action="store_true", help="also render page images (local only)")
    p.add_argument("--image-pages", default="", help="PDF pages to render, e.g. 20-40,55")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--no-pages", action="store_true", help="skip pages/pNNN.txt")
    p.add_argument("--force", action="store_true", help="re-extract even when nothing changed")
    p.set_defaults(func=run)
    return p


def _parse_pages(spec: str) -> list[int]:
    pages: list[int] = []
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, _, b = chunk.partition("-")
            pages.extend(range(int(a), int(b) + 1))
        else:
            pages.append(int(chunk))
    return pages


def run(args) -> int:
    campaign = None
    if not (args.out and args.pdf and args.book_id):
        campaign = load_campaign(args.campaign)  # raises with a hint when absent
    book_cfg = campaign.book if campaign is not None else {}
    book_id = args.book_id or (campaign.book_id if campaign is not None else "")
    if not book_id:
        raise SystemExit("error: no book id (pass one or set book.id in campaign.yaml)")
    pdf = args.pdf or (str(campaign.pdf_path) if campaign is not None and campaign.pdf_path else "")
    if not pdf:
        raise SystemExit("error: no --pdf and no book.pdf in campaign.yaml")
    pdf = os.path.expanduser(pdf)
    offset = args.offset if args.offset is not None else (campaign.offset if campaign else 0)
    if args.kinds:
        kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    elif book_cfg.get("kinds"):
        kinds = [str(k) for k in book_cfg["kinds"]]
    else:
        kinds = list(prepare_book.BOOK_KINDS)
    out_dir = args.out or str(campaign_paths(campaign.root, book_id).book_dir)

    payload = prepare_book.prepare(
        pdf,
        out_dir,
        book_id=book_id,
        offset=offset,
        kinds=kinds,
        edition=args.edition or str(book_cfg.get("edition") or ""),
        expect_sha=str(book_cfg.get("sha256") or ""),
        force=args.force,
        write_pages=not args.no_pages,
    )
    print(
        "book %s: %s  sha %s  pages %d  items %d  offset %d"
        % (
            book_id,
            "unchanged" if payload.get("skipped") else "prepared",
            payload["sha256"][:12],
            payload["pages"],
            payload["items"],
            payload["offset"],
        )
    )
    items = read_json(os.path.join(out_dir, "items.json"), default={}) or {}
    for kind, n in sorted((items.get("by_kind") or {}).items()):
        print("    %-12s %4d" % (kind, n))
    if items.get("ambiguous"):
        print("    %d items had more than one line-start occurrence (resolved by section span)"
              % len(items["ambiguous"]))

    errata_url = args.errata_url or (campaign.errata_url if campaign is not None else None)
    if errata_url and not args.no_errata:
        meta = fetch_errata.fetch(
            errata_url, out_dir, book_sha=payload["sha256"], offset=offset
        )
        print(
            "errata: HTTP %s  %s  sha %s  (%s)"
            % (
                meta["http_status"],
                meta["fetched_at"],
                meta["sha256"][:12],
                "changed" if meta["changed"] else "unchanged",
            )
        )

    if args.images:
        pages = _parse_pages(args.image_pages) or list(range(1, payload["pages"] + 1))
        manifest = render_pages.render(pdf, out_dir, pages, dpi=args.dpi, force=args.force)
        print("images: %d rendered, %d present (local only, gitignored)"
              % (len(manifest["written"]), len(manifest["present"])))

    if campaign is not None:
        stamp = {
            "book_id": book_id,
            "book_sha": payload["sha256"],
            "offset": offset,
            "pages": payload["pages"],
            "items": payload["items"],
        }
        for name in campaign.companion_names():
            state_merge(campaign, name, "book", dict(stamp),
                        top={"book_sha": payload["sha256"]})
    return 0


def main(argv=None) -> int:
    """Standalone entry point (until cf.py is wired up): ``python3 cfcmd/book.py …``."""
    import argparse

    parser = argparse.ArgumentParser(prog="cf")
    sub = parser.add_subparsers(dest="command", required=True)
    register(sub)
    args = parser.parse_args([NAME] + list(sys.argv[1:] if argv is None else argv))
    return run(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
