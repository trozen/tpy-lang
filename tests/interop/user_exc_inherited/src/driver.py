# Shared across ext-exec and cpy-parity: a subclass of a data-carrying user
# exception, raised transitively through a callee, crosses its own field
# (detail) AND its inherited field (code) as instance attributes, and is
# catchable as its user base. All parity-clean with CPython.
import excinherit

try:
    excinherit.run(-1)
    raise AssertionError("expected DerivedErr")
except excinherit.DerivedErr as e:
    print(type(e).__name__)
    print(isinstance(e, excinherit.BaseErr))
    print(e.code)     # inherited from BaseErr
    print(e.detail)   # own

print(excinherit.run(3))
