# A string annotation that doesn't parse as a Python expression must produce
# a clean tpyc diagnostic, not a CPython SyntaxError leaking through.
from tpy import int32


def f(x: "this is not @@@ syntax") -> int32:  # tpyc: error(/Cannot parse string type annotation/)
    return int32(0)


f(int32(1))
