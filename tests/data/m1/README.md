# M1 reference data

The ground truth for milestone M1's calibration test
(`tests/test_m1_calibration.py`, docs/proposals/pdf-to-companion.md section
5.2): the exported annotation documents of the two Tao companions, which carry
every numbered item and passage of Chapter 1 of the published edition (sha256
`64498329...`) with kind, number, pages and quotes. Format 1.0,
docs/annotations.md.

- `measure_theory.annotations.json.gz`: the measure_theory companion at master
  00a2438, exported by the M3 attribution-table exporter (432 items; numbered items
  are paged by their PDF regions).
- `tao-measure-lean.annotations.json.gz`: the tao-measure-lean companion at
  main f158509, exported by the M3 docstring-tags exporter (651 items; numbered
  items are paged by their text-match locations, passages by their "In-text"
  tags).

Both files are byte-for-byte copies of `tests/data/m3/` on the
`m3-exporters` branch (commit 1deba71), which is not merged; they are copied
here, not moved, and the M3 branch remains their origin. When that branch
merges, this directory can point at `tests/data/m3/` instead.

| file | sha256 |
|---|---|
| `measure_theory.annotations.json.gz` | `32f5bd91520d1be85bf58cb7d8f27f71be6b5e25d171abe7921e2a9f42ffbbfb` |
| `tao-measure-lean.annotations.json.gz` | `ed54527d06c016a60719198f936c9822d28fa29fd7da321b36fd322519933a90` |

They hold short heading and passage anchors (a few words each), the exception
docs/annotations.md section 11 allows for a companion's own document; no page
text, no PDF.
