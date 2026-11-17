# slugify keeps repeated whitespace

`slugify("Hello   big  World")` gives `hello---big--world`. Same with a custom
separator: `slugify('a  b', '_')` should be `a_b`.
