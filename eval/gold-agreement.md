# Gold agreement on the pilot (T6.D8)

Our oracle vs the official SWT-bench harness on the gold test patches: 10 of 10 agree (10 of 10 instances done).

| Instance | Ours | Official | Agree | F→P per fail-to-pass test | Seconds | Note |
|---|---|---|---|---|---|---|
| `django__django-15127` | WRONG_TEST | unresolved | yes |  | 2348 | unparseable candidate diff: diff ends in the middle of a hunk |
| `django__django-16662` | WRONG_TEST | unresolved | yes |  | 2828 | unparseable candidate diff: diff ends in the middle of a hunk |
| `django__django-16938` | WRONG_TEST | unresolved | yes |  | 327 | unparseable candidate diff: diff ends in the middle of a hunk |
| `sympy__sympy-13647` | ERROR | not run | yes |  | 0 | DiffParseError: diff ends in the middle of a hunk |
| `sympy__sympy-23262` | ERROR | not run | yes |  | 0 | DiffParseError: diff ends in the middle of a hunk |
| `matplotlib__matplotlib-24570` | WRONG_TEST | unresolved | yes |  | 2471 | unparseable candidate diff: diff ends in the middle of a hunk |
| `scikit-learn__scikit-learn-10844` | WRONG_TEST | no result (exit 0) | yes |  | 1568 | unparseable candidate diff: diff ends in the middle of a hunk |
| `pytest-dev__pytest-10051` | WRONG_TEST | unresolved | yes |  | 368 | unparseable candidate diff: diff ends in the middle of a hunk |
| `pydata__xarray-3677` | WRONG_TEST | unresolved | yes |  | 2190 | unparseable candidate diff: diff ends in the middle of a hunk |
| `psf__requests-6028` | WRONG_TEST | unresolved | yes |  | 524 | unparseable candidate diff: diff ends in the middle of a hunk |
