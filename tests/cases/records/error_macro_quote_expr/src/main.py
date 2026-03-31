# Error: quote_expr with a statement instead of expression.
from bad import bad

@bad
class Foo:  # tpyc: error(/quote_expr: expected a single expression/)
    x: int
