# A string annotation that doesn't parse as a Python expression must produce
# a clean tpyc diagnostic, not a CPython SyntaxError leaking through.
from tpy import Int32


def f(x: "this is not @@@ syntax") -> Int32:  # tpyc: error(/Cannot parse string type annotation/)
    return Int32(0)


f(Int32(1))
