# Ext-only: operator-protocol behavior the plain Python source doesn't share.
# A wrong-typed operand is intercepted BEFORE the method body runs (the C++
# signature requires the declared type), yielding CPython's own clean
# "unsupported operand type(s)" TypeError -- the plain source has no such
# guard and would instead raise whatever its first bad attribute access
# produces (here, an AttributeError from `"x".x` inside __add__'s body).
# 3-arg pow() is also ext-only: Vec2.__pow__ takes no modulus, so Python's
# own call machinery rejects a 3rd positional arg the same way our ternary
# nb_power slot does (a real modulus), but the exact error text differs.
import class_operators

v = class_operators.Vec2(1, 2)

try:
    v + "not a vec"
    raise AssertionError("expected TypeError on wrong-typed operand")
except TypeError:
    pass

try:
    pow(v, 2, 3)
    raise AssertionError("expected TypeError on 3-arg pow")
except TypeError:
    pass

# A right-TYPE but out-of-Int64-range operand must raise OverflowError, not
# be swallowed into NotImplemented by the MarshalError-downgrade path (which
# only downgrades a TypeError -- a type mismatch -- never a real error).
try:
    v * (2 ** 100)
    raise AssertionError("expected OverflowError on out-of-range operand")
except OverflowError:
    pass

print("ext-only operator checks: PASS")
