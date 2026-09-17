"""Re-export of the source stage's cflib adapter.

The pair stage consumes ``sources/<source_id>/items.json``, so it depends on
``sources`` anyway; keeping ONE adapter avoids two views of the item grammar.
Delete both this file and ``sources/compat.py`` when every call site has been
switched to ``cflib`` directly -- the names below already resolve to cflib
whenever it is importable.

The names below are the workspace model's throughout (``campaign_paths`` ->
``workspace_paths``, ``load_campaign`` -> ``load_workspace``, ``book_sha`` ->
``source_sha``); nothing in this package answers to the old vocabulary any more.
``workspace_paths(root).pairs_dir(source_id)`` takes the source, because a
workspace pairs each of its M sources into its own tree.
"""

from sources.compat import (  # noqa: F401
    DEFAULT_KINDS,
    DEFINITIONAL_KEYWORDS,
    DEFINITIONAL_KINDS,
    PAGE_ANCHOR,
    ItemId,
    workspace_paths,
    finding_id,
    item_sort_key,
    load_workspace,
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
