# Policy: sources, copyright and what may be committed

This is the rule the code implements, in one place, so that a licence
judgement about somebody else's book is not a sentence hidden in a YAML
comment. `registry/README.md` describes the registry's shape; this file
describes what it is allowed to say, and why.

The skill's own code and metadata are Apache-2.0 (`LICENSE`). **That licence
covers nothing about the sources.** A source is somebody's copyrighted work,
and nothing here grants any right in it.

## 1. Metadata only, never a line of the book

The registry, this repository and every artifact that leaves a workspace hold
**facts about** an edition — title, authors, edition label, sha256, byte count,
page count, page offset, item kinds, URLs, page maps, licence notes — and never
a line **of** it.

- No book text, no quotations, no extracted page snippets, no items list, no
  page images or crops.
- The prose lives, gitignored, in a workspace's `sources/<source_id>/`, on the
  machine of somebody who already holds that edition.
- `cf sources validate-registry` is the mechanical half: it refuses an unknown
  field and any string outside the four free-text fields that runs past a word
  cap or into a second sentence. The rule was stated as hard for months with
  nothing enforcing it.

## 2. Fetching: the author's own site, once, with a note

A download happens only when **all three** hold
(`RegistryEntry.may_fetch`, `cf onboard --download`):

1. `--download` was passed — nothing is ever fetched implicitly;
2. the workspace's `policy.allow_download` is not false;
3. the registry entry's `licence.fetch` is `author_site` or `open`, beside a
   `licence.note` saying in plain words why. `fetch: none` refuses even with
   the flag.

`author_site` means exactly what it says: the author publishes that file on a
site he controls, and the fetch is the same GET a reader would make. A copy on
a file-sharing site, a course page that mirrors a publisher's PDF, or a library
proxy is **not** `author_site`, whatever its URL looks like.

## 3. Never redistributed

`licence.redistribute` is `false` for every entry and `Registry.validate()`
reports an entry that says otherwise as a problem. Nothing this skill fetches
is re-served, committed, attached to an issue, or copied to a host that did not
already have it — with one documented exception, which is not redistribution:
`cf run` transfers the prepared source text to the **audit host the operator
named and consented to**, because the gate must read it there. That transfer is
recorded in `run.yaml` with the consent.

## 4. Quotation inside the audit

A finding quotes the source because a faithfulness claim that cannot be checked
against the text is not a finding. The limits:

- **one or two sentences**, and the gate records anything over 600 characters
  as over-long and refuses a quote over 3,000 (`SKILL.md` hard rules 1 and 6);
- a quote is checked against the pinned `text.txt` of the edition the row
  names, so a quote that is not in the book fails the gate;
- **what leaves the workspace is a property of the destination.** The owner of
  the repository under audit gets the sentence (`--destination owner`); a
  public tracker gets the printed page, six words at each end and a hash
  (`--destination public`). It is never inferred from a git remote.
- `cf share export` redacts every quote by default, including a verbatim run of
  the source inside a `reason` or a `reasoning`. A reader who holds the same
  edition recovers it with `cf quotes hydrate`, out of their own copy.

## 5. Contributing an edition

By opening a PR that adds a registry entry you are asserting that:

- you computed the `sha256` from bytes you hold yourself — never a sha quoted
  from somebody's prose, which belongs in `aliases.sha256_prefixes`;
- the entry contains no text of the work;
- any `download_url` is the author's own publication of that file, and the
  `licence.note` says so in words a reader can check;
- you are not the copyright holder speaking for the publisher, and the note
  records a judgement rather than a permission.

`cf sources promote <id>` writes the entry as a patch file for exactly this
purpose; nothing writes the shipped registry from a workspace.

## 6. If a rights holder objects

Open an issue, or write to the maintainers. The remedy is small and immediate,
because the registry holds nothing but metadata: the entry's `download_url` and
`licence.fetch` are removed (the edition stays identifiable by sha, which is
what the audit needs), or the entry is deleted outright. No book text has to be
taken down, because none was ever published here.
