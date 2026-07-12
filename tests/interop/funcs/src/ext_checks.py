# Ext-only: the compiled extension enforces Int64 bounds and integer typing at
# the boundary (marshalling errors -> Python exceptions). These deliberately
# do NOT match the CPython source (where `Int64` is just an annotation and
# `int` is unbounded), so they are checked only against the built .so.
import funcs


def expect(exc, f):
    try:
        f()
    except exc:
        return "ok"
    return "NO-RAISE"


assert expect(OverflowError, lambda: funcs.add(2**70, 0)) == "ok"    # > int64 max
assert expect(OverflowError, lambda: funcs.add(-(2**70), 0)) == "ok"  # < int64 min
assert expect(TypeError, lambda: funcs.add("x", 1)) == "ok"          # not an int
assert expect(TypeError, lambda: funcs.add(1.5, 1)) == "ok"          # float: no __index__
assert expect(TypeError, lambda: funcs.big_square(1.5)) == "ok"      # BigInt param, float
assert expect(TypeError, lambda: funcs.negate("x")) == "ok"          # BigInt param, str
print("ext-only error checks: PASS")
