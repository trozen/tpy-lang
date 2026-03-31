# Error: quote_fun with no function definition in source string.
from bad import bad

@bad
class Foo:  # tpyc: error(/quote_fun: expected exactly 1 function definition/)
    x: int
