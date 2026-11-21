# Gold agreement on the pilot (T6.D8)

Gold tests F→P in our oracle and resolved by the official SWT-bench harness: 3 of 10. Verdicts agree on 5 of 10 (10 of 10 instances done).

| Instance | Ours | Official | Agree | F→P per fail-to-pass test | Seconds | Note |
|---|---|---|---|---|---|---|
| `django__django-15127` | WRONG_TEST | unresolved | yes | test_override_settings_level_tags E→E | 394 | O1 failed: tests/messages_tests/tests.py::TestLevelTags::test_override_settings_level_tags E→E; O2 failed: tests/messages_tests/base.py E→E, tests/messages_test |
| `django__django-16662` | WRONG_TEST | unresolved | yes |  | 422 | O2 failed: tests/migrations/test_writer.py E→E |
| `django__django-16938` | WRONG_TEST | resolved | NO | test_serialize_no_only_pk_with_natural_keys E→E, test_serialize_only_pk E→E, test_serialize_prefetch_related_m2m E→E, test_serialize_progressbar E→E, test_serialize_no_only_pk_with_natural_keys E→E, test_serialize_only_pk E→E, test_serialize_prefetch_related_m2m E→E, test_serialize_progressbar E→E, test_serialize_no_only_pk_with_natural_keys E→E, test_serialize_only_pk E→E, test_serialize_prefetch_related_m2m E→E, test_serialize_progressbar E→E, test_serialize_no_only_pk_with_natural_keys E→E, test_serialize_only_pk E→E, test_serialize_prefetch_related_m2m E→E, test_serialize_progressbar E→E | 334 | O1 failed: tests/serializers/test_json.py::JsonSerializerTestCase::test_serialize_no_only_pk_with_natural_keys E→E, tests/serializers/test_json.py::JsonSerializ |
| `sympy__sympy-13647` | WRONG_TEST | resolved | NO | test_col_insert E→E | 369 | O1 failed: sympy/matrices/tests/test_commonmatrix.py::test_col_insert E→E; O2 failed: sympy/matrices/tests/test_commonmatrix.py E→E |
| `sympy__sympy-23262` | WRONG_TEST | resolved | NO | test_issue_14941 E→E | 411 | O1 failed: sympy/utilities/tests/test_lambdify.py::test_issue_14941 E→E; O2 failed: sympy/utilities/tests/test_lambdify.py E→E |
| `matplotlib__matplotlib-24570` | WRONG_TEST | resolved | NO | test_packers[bottom] F→P, test_packers[top] F→P | 2587 | O2 failed: lib/matplotlib/tests/test_offsetbox.py::test_anchoredtext_horizontal_alignment[png] F→F, lib/matplotlib/tests/test_offsetbox.py::test_annotationbbox_ |
| `scikit-learn__scikit-learn-10844` | REPRODUCED | resolved | yes | test_int_overflow_mutual_info_fowlkes_mallows_score F→P | 650 |  |
| `pytest-dev__pytest-10051` | REPRODUCED | resolved | yes | test_clear_for_call_stage F→P | 347 |  |
| `pydata__xarray-3677` | REPRODUCED | resolved | yes | test_merge_dataarray F→P | 1369 |  |
| `psf__requests-6028` | WRONG_TEST | resolved | NO | test_prepend_scheme_if_needed[http://user:pass@example.com/path?query-http://user:pass@example.com/path?query] F→P, test_prepend_scheme_if_needed[http://user@example.com/path?query-http://user@example.com/path?query] F→P | 301 | O2 failed: tests/test_utils.py::test_should_bypass_proxies_pass_only_hostname[http://172.16.1.1/-172.16.1.1] E→E, tests/test_utils.py::test_should_bypass_proxie |
