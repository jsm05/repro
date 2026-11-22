# Gold agreement on the pilot (T6.D8)

Gold tests F→P in our oracle and resolved by the official SWT-bench harness: 7 of 10. Verdicts agree on 7 of 10 (10 of 10 instances done).

| Instance | Ours | Official | Agree | F→P per fail-to-pass test | Seconds | Note |
|---|---|---|---|---|---|---|
| `django__django-15127` | REPRODUCED | unresolved | NO | test_override_settings_level_tags F→P | 428 |  |
| `django__django-16662` | REPRODUCED | unresolved | NO | #24155 - Tests ordering of imports. F→P | 391 |  |
| `django__django-16938` | REPRODUCED | resolved | yes | Basic serialization works. F→P, Deserialized content can be saved with force_insert as a parameter. F→P, Mapping such as fields should be deterministically ordered. (#24558) F→P, Serialized content can be deserialized. F→P, Serializing control characters with XML should fail as those characters F→P, The ability to create new objects by modifying serialized content. F→P, Year values before 1000AD are properly formatted F→P, test_serialize_no_only_pk_with_natural_keys F→P, test_serialize_only_pk F→P, test_serialize_prefetch_related_m2m F→P, test_serialize_progressbar F→P, test_serialize_no_only_pk_with_natural_keys F→P, test_serialize_only_pk F→P, test_serialize_prefetch_related_m2m F→P, test_serialize_progressbar F→P, test_serialize_no_only_pk_with_natural_keys F→P, test_serialize_only_pk F→P, test_serialize_prefetch_related_m2m F→P, test_serialize_progressbar F→P, test_serialize_no_only_pk_with_natural_keys F→P, test_serialize_only_pk F→P, test_serialize_prefetch_related_m2m F→P, test_serialize_progressbar F→P | 434 |  |
| `sympy__sympy-13647` | REPRODUCED | resolved | yes | test_col_insert F→P | 450 |  |
| `sympy__sympy-23262` | REPRODUCED | resolved | yes | test_issue_14941 F→P | 394 |  |
| `matplotlib__matplotlib-24570` | WRONG_TEST | resolved | NO | test_packers[bottom] F→P, test_packers[top] F→P | 2687 | O2 failed: lib/matplotlib/tests/test_offsetbox.py::test_picking[axes points-text] P→~ |
| `scikit-learn__scikit-learn-10844` | REPRODUCED | resolved | yes | test_int_overflow_mutual_info_fowlkes_mallows_score F→P | 726 |  |
| `pytest-dev__pytest-10051` | REPRODUCED | resolved | yes | test_clear_for_call_stage F→P | 288 |  |
| `pydata__xarray-3677` | REPRODUCED | resolved | yes | test_merge_dataarray F→P | 2366 |  |
| `psf__requests-6028` | REPRODUCED | resolved | yes | test_prepend_scheme_if_needed[http://user:pass@example.com/path?query-http://user:pass@example.com/path?query] F→P, test_prepend_scheme_if_needed[http://user@example.com/path?query-http://user@example.com/path?query] F→P | 348 |  |
