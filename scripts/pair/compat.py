"""Re-export of the source stage's cflib adapter.

The pair stage consumes ``sources/<source_id>/items.json``, so it depends on
``sources`` anyway; keeping ONE adapter avoids two views of the item grammar.
Delete both this file and ``sources/compat.py`` when every call site has been
switched to ``cflib`` directly -- the names below already resolve to cflib
whenever it is importable.

(The two renamed names below are the workspace model's: ``campaign_paths`` ->
``workspace_paths``, ``load_campaign`` -> ``load_workspace``. The rest of this
package still speaks the old vocabulary and is migrated by the STAGES-A package.)
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
