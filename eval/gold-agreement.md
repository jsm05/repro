# Gold agreement on the pilot (T6.D8)

Gold tests F→P in our oracle and the official SWT-bench harness agrees: 10 of 10 (8 outright, 2 as explained exceptions below). Raw verdicts match on 8 of 10 (10 of 10 instances done).

| Instance | Ours | Official | Agree | F→P per fail-to-pass test | Seconds | Note |
|---|---|---|---|---|---|---|
| `django__django-15127` | REPRODUCED | unresolved | explained | test_override_settings_level_tags F→P | 856 |  |
| `django__django-16662` | REPRODUCED | unresolved | explained | #24155 - Tests ordering of imports. F→P | 359 |  |
| `django__django-16938` | REPRODUCED | resolved | yes | Basic serialization works. F→P, Deserialized content can be saved with force_insert as a parameter. F→P, Mapping such as fields should be deterministically ordered. (#24558) F→P, Serialized content can be deserialized. F→P, Serializing control characters with XML should fail as those characters F→P, The ability to create new objects by modifying serialized content. F→P, Year values before 1000AD are properly formatted F→P, test_serialize_no_only_pk_with_natural_keys F→P, test_serialize_only_pk F→P, test_serialize_prefetch_related_m2m F→P, test_serialize_progressbar F→P, test_serialize_no_only_pk_with_natural_keys F→P, test_serialize_only_pk F→P, test_serialize_prefetch_related_m2m F→P, test_serialize_progressbar F→P, test_serialize_no_only_pk_with_natural_keys F→P, test_serialize_only_pk F→P, test_serialize_prefetch_related_m2m F→P, test_serialize_progressbar F→P, test_serialize_no_only_pk_with_natural_keys F→P, test_serialize_only_pk F→P, test_serialize_prefetch_related_m2m F→P, test_serialize_progressbar F→P | 369 |  |
| `sympy__sympy-13647` | REPRODUCED | resolved | yes | test_col_insert F→P | 445 |  |
| `sympy__sympy-23262` | REPRODUCED | resolved | yes | test_issue_14941 F→P | 330 |  |
| `matplotlib__matplotlib-24570` | REPRODUCED | resolved | yes | test_packers[bottom] F→P, test_packers[top] F→P | 2698 |  |
| `scikit-learn__scikit-learn-10844` | REPRODUCED | resolved | yes | test_int_overflow_mutual_info_fowlkes_mallows_score F→P | 744 |  |
| `pytest-dev__pytest-10051` | REPRODUCED | resolved | yes | test_clear_for_call_stage F→P | 363 |  |
| `pydata__xarray-3677` | REPRODUCED | resolved | yes | test_merge_dataarray F→P | 1082 |  |
| `psf__requests-6028` | REPRODUCED | resolved | yes | test_prepend_scheme_if_needed[http://user:pass@example.com/path?query-http://user:pass@example.com/path?query] F→P, test_prepend_scheme_if_needed[http://user@example.com/path?query-http://user@example.com/path?query] F→P | 292 |  |

## Explained exceptions

Our oracle shows the gold tests F→P; the official harness disagrees only because of how it read its own log. Listed in `eval/gold-exceptions.yaml`.

**`django__django-15127`** (official: unresolved)

- Why: The official harness's Django parser read a pip install line as a test. Its base run lists a PASS_TO_FAIL "test" named "testbed/lib/python3.9/site-packages (from Django==4.1.dev20211125193604)", and that alone marks the instance unresolved. The gold test itself goes F→P in its own report.
- Evidence: run_instance_swt_logs/gold-django__django-15127/gold/django__django-15127/report.json: tests_base.PASS_TO_FAIL is the pip line; tests_pred.FAIL_TO_PASS has test_override_settings_level_tags (messages_tests.tests.TestLevelTags).

**`django__django-16662`** (official: unresolved)

- Why: The gold test has a docstring, so Django prints its result on the line after its name ("#24155 - Tests ordering of imports. ... ok"). The official parser only reads names that start with "test", drops that line, and ends up with an empty FAIL_TO_PASS. Repro reads those pairs (D-M6-16).
- Evidence: run_instance_swt_logs/gold-django__django-16662/pred_post__gold/django__django-16662/test_output.txt shows "test_sorted_imports (migrations.test_writer.WriterTests.test_sorted_imports)" then "#24155 - Tests ordering of imports. ... ok"; report.json has no FAIL_TO_PASS entry for it.

