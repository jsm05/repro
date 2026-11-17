# paginate returns one item too few

`paginate([1, 2, 3, 4, 5], 0, 2)` gives `[1]`. With a page size of 2 I expect two
items on every page except possibly the last one.
