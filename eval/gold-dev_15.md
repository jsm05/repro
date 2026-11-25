# Gold agreement on `dev_15` (T6.D8)

Gold tests F→P in our oracle and the official SWT-bench harness agrees: 12 of 15 (12 outright, 0 as explained exceptions below). Raw verdicts match on 14 of 15 (15 of 15 instances done).

| Instance | Ours | Official | Agree | F→P per fail-to-pass test | Seconds | Note |
|---|---|---|---|---|---|---|
| `django__django-16032` | REPRODUCED | resolved | yes | test_annotation_and_alias_filter_in_subquery F→P, test_annotation_and_alias_filter_related_in_subquery F→P | 354 |  |
| `django__django-12273` | REPRODUCED | resolved | yes | test_create_new_instance_with_pk_equals_none F→P, test_create_new_instance_with_pk_equals_none_multi_inheritance F→P | 439 |  |
| `django__django-13343` | REPRODUCED | resolved | yes | test_deconstruction F→P | 521 |  |
| `django__django-15629` | REPRODUCED | resolved | yes | AlterField operation of db_collation on primary keys changes any FKs F→P, Creation of models with a FK to a PK with db_collation. F→P | 360 |  |
| `sympy__sympy-17318` | REPRODUCED | resolved | yes | test_issue_12420 F→P | 522 |  |
| `django__django-16801` | REPRODUCED | resolved | yes | test_post_init_not_connected F→P | 433 |  |
| `django__django-12965` | REPRODUCED | resolved | yes | test_fast_delete_all F→P | 443 |  |
| `matplotlib__matplotlib-23476` | WRONG_TEST | unresolved | yes | test_unpickle_with_device_pixel_ratio F→F | 1425 | O1 failed: lib/matplotlib/tests/test_figure.py::test_unpickle_with_device_pixel_ratio F→F |
| `scikit-learn__scikit-learn-10297` | REPRODUCED | resolved | yes | test_ridge_classifier_cv_store_cv_values F→P | 751 |  |
| `sphinx-doc__sphinx-8721` | WRONG_TEST | unresolved | yes | test_viewcode_epub_default E→E | 461 | O1 failed: tests/test_ext_viewcode.py::test_viewcode_epub_default E→E |
| `sympy__sympy-17139` | REPRODUCED | resolved | yes | test__TR56 F→P, test_issue_17137 F→P | 951 |  |
| `sympy__sympy-15976` | REPRODUCED | resolved | yes | test_presentation_symbol F→P | 375 |  |
| `pydata__xarray-3151` | REPRODUCED | resolved | yes | test_combine_leaving_bystander_dimensions F→P | 1116 |  |
| `django__django-14771` | REPRODUCED | unresolved | NO | test_xoptions F→P | 439 |  |
| `astropy__astropy-14369` | REPRODUCED | resolved | yes | test_cds_grammar[strings4-unit4] F→P, test_cds_grammar[strings6-unit6] F→P, test_cds_grammar_fail[km/s.Mpc-1] F→P | 537 |  |
