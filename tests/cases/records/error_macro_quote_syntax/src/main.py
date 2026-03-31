# Error: syntax error in quoted source string.
from bad import bad

@bad
class Foo:  # tpyc: error(/syntax error in quoted source/)
    x: int
