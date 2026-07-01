# Shared across the ext-exec and cpy-parity runs: repr/eq/ne/lt/hash on Vec2
# must behave like a plain Python class with the same dunder bodies, and Frac
# (eq without hash) must be unhashable on both the compiled type and the
# plain source -- CPython's own type-creation machinery does this for a
# source-level class too, so it is source-parity, not an ext-only divergence.
import class_dunders

a = class_dunders.Vec2(1, 2)
b = class_dunders.Vec2(1, 2)
c = class_dunders.Vec2(3, 4)

print(repr(a))                  # Vec2(1, 2)
print(str(a))                   # Vec2(1, 2) -- no __str__, falls back to __repr__
print(a == b, a == c)           # True False
print(a != b, a != c)           # False True -- __ne__ auto-derived from __eq__
print(a < c, c < a)             # True False

print(hash(a) == hash(b))       # True -- equal values hash equal
s = {a, b, c}
print(len(s))                   # 2 -- a and b are equal, dedup in the set

f1 = class_dunders.Frac(1, 2)
f2 = class_dunders.Frac(1, 2)
print(f1 == f2)                 # True
try:
    hash(f1)
    raise AssertionError("expected TypeError: unhashable")
except TypeError:
    print("frac unhashable: PASS")

o1 = class_dunders.Ordered(1)
o2 = class_dunders.Ordered(2)
print(o1 < o2, o2 < o1)         # True False
# hash(o1) is deliberately NOT exercised here -- it's an ext-only divergence
# (see ext_checks.py): the plain Python source stays hashable (only __eq__
# unhashes a class-statement type), but the compiled type does not (PyType_
# FromSpec nulls the hash whenever richcompare is populated at all).
