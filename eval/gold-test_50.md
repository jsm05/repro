# Gold agreement on `test_50` (T6.D8)

Gold tests F→P in our oracle and the official SWT-bench harness agrees: 41 of 50 (41 outright, 0 as explained exceptions below). Raw verdicts match on 44 of 50 (50 of 50 instances done).

| Instance | Ours | Official | Agree | F→P per fail-to-pass test | Seconds | Note |
|---|---|---|---|---|---|---|
| `django__django-11138` | REPRODUCED | resolved | yes | test_query_convert_timezones F→P | 816 |  |
| `sphinx-doc__sphinx-9281` | REPRODUCED | unresolved | NO | test_object_description_enum F→P | 443 |  |
| `pylint-dev__pylint-8898` | REPRODUCED | resolved | yes | test_csv_regex_error F→P | 451 |  |
| `django__django-13344` | REPRODUCED | resolved | yes | test_coroutine F→P, test_deprecation E→P | 1509 |  |
| `pytest-dev__pytest-7432` | REPRODUCED | resolved | yes | test_xfail_run_with_skip_mark[test_input1-expected1] F→P | 385 |  |
| `django__django-9296` | REPRODUCED | resolved | yes | test_paginator_iteration F→P | 583 |  |
| `django__django-16429` | REPRODUCED | resolved | yes | test_depth F→P, test_depth_invalid E→P, test_thousand_years_ago F→P, Test other units. P→P | 522 |  |
| `sympy__sympy-12419` | REPRODUCED | resolved | yes | test_Identity F→P | 370 |  |
| `django__django-16315` | REPRODUCED | resolved | yes | test_update_conflicts_unique_fields_update_fields_db_column F→P | 899 |  |
| `django__django-11163` | REPRODUCED | resolved | yes | test_modelform_subclassed_model F→P | 338 |  |
| `astropy__astropy-14365` | REPRODUCED | resolved | yes | test_roundtrip[True] F→P | 517 |  |
| `django__django-13297` | REPRODUCED | unresolved | NO | test_template_params_filtering F→P | 467 |  |
| `sympy__sympy-22456` | REPRODUCED | resolved | yes | test_String F→P | 317 |  |
| `sphinx-doc__sphinx-9658` | REPRODUCED | unresolved | NO | test_restify_mock F→P | 467 |  |
| `scikit-learn__scikit-learn-13328` | REPRODUCED | resolved | yes | test_huber_bool F→P | 858 |  |
| `pydata__xarray-4687` | REPRODUCED | resolved | yes | test_where_attrs F→P | 1422 |  |
| `django__django-12155` | REPRODUCED | resolved | yes | test_parse_rst_with_docstring_no_leading_line_feed F→P | 783 |  |
| `sympy__sympy-19040` | REPRODUCED | resolved | yes | test_issue_5786 F→P | 611 |  |
| `django__django-14434` | REPRODUCED | resolved | yes | test_unique_constraint F→P | 556 |  |
| `django__django-14238` | REPRODUCED | resolved | yes | test_issubclass_of_autofield F→P, test_default_auto_field_setting_bigautofield_subclass F→P | 1461 |  |
| `django__django-11119` | REPRODUCED | unresolved | NO | test_autoescape_off F→P | 426 |  |
| `matplotlib__matplotlib-21568` | REPRODUCED | resolved | yes | test_concise_formatter_usetex[t_delta2-expected2] F→P, test_concise_formatter_usetex[t_delta3-expected3] F→P, test_date_formatter_usetex[delta2-expected2] F→P, test_date_formatter_usetex[delta3-expected3] F→P | 1134 |  |
| `astropy__astropy-12907` | REPRODUCED | resolved | yes | test_separable[compound_model6-result6] F→P, test_separable[compound_model9-result9] F→P | 675 |  |
| `sympy__sympy-24443` | REPRODUCED | resolved | yes | test_homomorphism F→P | 333 |  |
| `matplotlib__matplotlib-24177` | REPRODUCED | resolved | yes | test_small_autoscale F→P | 2113 |  |
| `matplotlib__matplotlib-24637` | WRONG_TEST | unresolved | yes | test_annotationbbox_gid F→F | 1320 | O1 failed: lib/matplotlib/tests/test_backend_svg.py::test_annotationbbox_gid F→F |
| `django__django-14752` | REPRODUCED | resolved | yes | test_serialize_result F→P | 449 |  |
| `sympy__sympy-13974` | REPRODUCED | resolved | yes | test_tensor_product_simp F→P | 306 |  |
| `sympy__sympy-24661` | REPRODUCED | resolved | yes | test_issue_24288 F→P | 512 |  |
| `django__django-15554` | REPRODUCED | resolved | yes | test_multiple F→P | 429 |  |
| `django__django-15375` | REPRODUCED | resolved | yes | test_aggregation_default_after_annotation F→P | 375 |  |
| `django__django-16485` | REPRODUCED | unresolved | NO | test_zero_values F→P | 355 |  |
| `django__django-16502` | REPRODUCED | unresolved | NO | test_no_body_returned_for_head_requests F→P | 408 |  |
| `django__django-11087` | REPRODUCED | resolved | yes | test_only_referenced_fields_selected F→P | 523 |  |
| `pytest-dev__pytest-5787` | REPRODUCED | resolved | yes | test_chained_exceptions[CollectReport] F→P, test_chained_exceptions[TestReport] F→P | 589 |  |
| `scikit-learn__scikit-learn-12973` | REPRODUCED | resolved | yes | test_lasso_lars_fit_copyX_behaviour[False] F→P | 1041 |  |
| `django__django-13820` | REPRODUCED | resolved | yes | test_loading_package_without__file__ F→P | 391 |  |
| `django__django-12858` | REPRODUCED | resolved | yes | test_ordering_pointing_to_lookup_not_transform F→P | 816 |  |
| `pydata__xarray-4356` | REPRODUCED | resolved | yes | test_min_count_nd[prod-False-bool_] F→P, test_min_count_nd[prod-False-float32] F→P, test_min_count_nd[prod-False-float] F→P, test_min_count_nd[prod-False-int] F→P, test_min_count_nd[sum-False-bool_] F→P, test_min_count_nd[sum-False-float32] F→P, test_min_count_nd[sum-False-float] F→P, test_min_count_nd[sum-False-int] F→P | 1245 |  |
| `django__django-15525` | REPRODUCED | resolved | yes | Natural keys with foreing keys in dependencies works in a multiple F→P | 413 |  |
| `sympy__sympy-21847` | REPRODUCED | resolved | yes | test_monomials F→P | 384 |  |
| `sphinx-doc__sphinx-8548` | WRONG_TEST | unresolved | yes | test_inherited_instance_variable E→E | 476 | O1 failed: tests/test_ext_autodoc_autoclass.py::test_inherited_instance_variable E→E |
| `django__django-15098` | REPRODUCED | resolved | yes | test_get_language_from_path_real F→P, test_get_supported_language_variant_null E→P | 374 |  |
| `sympy__sympy-20590` | REPRODUCED | resolved | yes | test_immutable F→P | 853 |  |
| `scikit-learn__scikit-learn-15100` | REPRODUCED | resolved | yes | test_strip_accents F→P | 825 |  |
| `django__django-11728` | REPRODUCED | resolved | yes | test_simplify_regex F→P, test_app_not_found P→P | 451 |  |
| `matplotlib__matplotlib-23412` | WRONG_TEST | unresolved | yes | test_dash_offset_patch_draw[png] F→F | 1054 | O1 failed: lib/matplotlib/tests/test_patches.py::test_dash_offset_patch_draw[png] F→F |
| `django__django-15382` | REPRODUCED | resolved | yes | test_negated_empty_exists F→P | 762 |  |
| `django__django-15467` | REPRODUCED | resolved | yes | test_radio_fields_foreignkey_formfield_overrides_empty_label F→P | 649 |  |
| `django__django-15732` | REPRODUCED | resolved | yes | test_remove_unique_together_on_unique_field F→P | 620 |  |
