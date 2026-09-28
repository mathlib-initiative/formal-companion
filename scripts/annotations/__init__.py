"""The skill's tooling around the annotation format: exporters and the tag review.

``export`` holds the companion exporters behind ``cf export`` and
``review_tags`` the tag review behind ``cf review-tags``.  The format itself
(its schema, checker, id rules and vocabulary) is the vendored
``formal_companion_annotations`` package under ``vendor/``, imported through
:mod:`cflib.vendored`; its specification is formal-companion-annotations'
docs/annotations.md.
"""
