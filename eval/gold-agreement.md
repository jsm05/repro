# Gold agreement on the pilot (T6.D8)

Gold tests F→P in our oracle and resolved by the official SWT-bench harness: 6 of 10. Verdicts agree on 7 of 10 (10 of 10 instances done).

| Instance | Ours | Official | Agree | F→P per fail-to-pass test | Seconds | Note |
|---|---|---|---|---|---|---|
| `django__django-15127` | REPRODUCED | unresolved | NO | test_override_settings_level_tags F→P | 327 |  |
| `django__django-16662` | WRONG_TEST | unresolved | yes |  | 457 | no candidate test goes F→P |
| `django__django-16938` | WRONG_TEST | resolved | NO | test_serialize_no_only_pk_with_natural_keys F→P, test_serialize_only_pk F→P, test_serialize_prefetch_related_m2m F→P, test_serialize_progressbar F→P, test_serialize_no_only_pk_with_natural_keys E→E, test_serialize_only_pk E→E, test_serialize_prefetch_related_m2m E→E, test_serialize_progressbar E→E, test_serialize_no_only_pk_with_natural_keys E→P, test_serialize_only_pk E→P, test_serialize_prefetch_related_m2m E→P, test_serialize_progressbar E→P, test_serialize_no_only_pk_with_natural_keys E→P, test_serialize_only_pk E→P, test_serialize_prefetch_related_m2m E→P, test_serialize_progressbar F→P | 410 | O1 failed: tests/serializers/test_jsonl.py::JsonlSerializerTestCase::test_serialize_no_only_pk_with_natural_keys E→E, tests/serializers/test_jsonl.py::JsonlSeri |
| `sympy__sympy-13647` | REPRODUCED | resolved | yes | test_col_insert F→P | 348 |  |
| `sympy__sympy-23262` | REPRODUCED | resolved | yes | test_issue_14941 F→P | 392 |  |
| `scikit-learn__scikit-learn-10844` | REPRODUCED | resolved | yes | test_int_overflow_mutual_info_fowlkes_mallows_score F→P | 557 |  |
| `pytest-dev__pytest-10051` | REPRODUCED | resolved | yes | test_clear_for_call_stage F→P | 367 |  |
| `pydata__xarray-3677` | REPRODUCED | resolved | yes | test_merge_dataarray F→P | 1252 |  |
| `psf__requests-6028` | REPRODUCED | resolved | yes | test_prepend_scheme_if_needed[http://user:pass@example.com/path?query-http://user:pass@example.com/path?query] F→P, test_prepend_scheme_if_needed[http://user@example.com/path?query-http://user@example.com/path?query] F→P | 371 |  |
| `matplotlib__matplotlib-24570` | not run | not run | NO |  | 0 | no result from its job |
