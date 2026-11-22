# Gold agreement on the pilot (T6.D8)

Gold tests F→P in our oracle and resolved by the official SWT-bench harness: 6 of 10. Verdicts agree on 8 of 10 (10 of 10 instances done).

| Instance | Ours | Official | Agree | F→P per fail-to-pass test | Seconds | Note |
|---|---|---|---|---|---|---|
| `django__django-15127` | REPRODUCED | unresolved | NO | test_override_settings_level_tags F→P | 416 |  |
| `django__django-16662` | WRONG_TEST | unresolved | yes | #24155 - Tests ordering of imports. E→E | 401 | O1 failed: tests/migrations/test_writer.py::#24155 - Tests ordering of imports. E→E |
| `django__django-16938` | WRONG_TEST | resolved | NO | Basic serialization works. E→E, Deserialized content can be saved with force_insert as a parameter. E→E, Mapping such as fields should be deterministically ordered. (#24558) E→E, Serialized content can be deserialized. E→E, Serializing control characters with XML should fail as those characters E→E, The ability to create new objects by modifying serialized content. E→E, Year values before 1000AD are properly formatted E→E, test_serialize_no_only_pk_with_natural_keys F→P, test_serialize_only_pk F→P, test_serialize_prefetch_related_m2m F→P, test_serialize_progressbar F→P, test_serialize_no_only_pk_with_natural_keys E→E, test_serialize_only_pk E→E, test_serialize_prefetch_related_m2m E→E, test_serialize_progressbar E→E, test_serialize_no_only_pk_with_natural_keys E→P, test_serialize_only_pk E→P, test_serialize_prefetch_related_m2m E→P, test_serialize_progressbar E→P, test_serialize_no_only_pk_with_natural_keys E→P, test_serialize_only_pk E→P, test_serialize_prefetch_related_m2m E→P, test_serialize_progressbar F→P | 351 | O1 failed: tests/serializers/models/base.py::The ability to create new objects by modifying serialized content. E→E, tests/serializers/models/base.py::Deseriali |
| `sympy__sympy-13647` | REPRODUCED | resolved | yes | test_col_insert F→P | 441 |  |
| `sympy__sympy-23262` | REPRODUCED | resolved | yes | test_issue_14941 F→P | 337 |  |
| `matplotlib__matplotlib-24570` | ERROR | not run | yes |  | 5400 | TimeoutExpired: Command '['/opt/hostedtoolcache/Python/3.11.18/x64/bin/python', '/home/runner/work/repro/repro/src/repro/datasets/_swt_driver.py']' timed out af |
| `scikit-learn__scikit-learn-10844` | REPRODUCED | resolved | yes | test_int_overflow_mutual_info_fowlkes_mallows_score F→P | 740 |  |
| `pytest-dev__pytest-10051` | REPRODUCED | resolved | yes | test_clear_for_call_stage F→P | 360 |  |
| `pydata__xarray-3677` | REPRODUCED | resolved | yes | test_merge_dataarray F→P | 1362 |  |
| `psf__requests-6028` | REPRODUCED | resolved | yes | test_prepend_scheme_if_needed[http://user:pass@example.com/path?query-http://user:pass@example.com/path?query] F→P, test_prepend_scheme_if_needed[http://user@example.com/path?query-http://user@example.com/path?query] F→P | 299 |  |
