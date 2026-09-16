"""Re-export of the book stage's cflib adapter.

The pair stage consumes the book stage's ``items.json``, so it depends on
``book`` anyway; keeping ONE adapter avoids two views of the item grammar.
Delete both this file and ``book/compat.py`` when every call site has been
switched to ``cflib`` directly -- the names below already resolve to cflib
whenever it is importable.
"""

from book.compat import (  # noqa: F401
    DEFAULT_KINDS,
    PAGE_ANCHOR,
    ItemId,
    campaign_paths,
    finding_id,
    item_sort_key,
    load_campaign,
    load_yaml,
    normalise_item,
    parse_item,
    parse_item_reject,
    parse_pages,
    read_json,
    read_jsonl,
    sha256_file,
    sha256_text,
    state_merge,
    utcnow,
    write_json,
    write_jsonl,
    write_text,
)
