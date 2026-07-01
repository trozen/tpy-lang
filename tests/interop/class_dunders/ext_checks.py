# Ext-only: comparing a Vec2 against a type its dunders don't declare. The
# compiled richcompare slot type-checks the other operand BEFORE calling into
# __eq__/__lt__ (the C++ method needs a Vec2&, so there's no way to call it
# with a str) and returns NotImplemented on a mismatch, same as CPython's own
# comparison protocol. The plain Python source has no such guard -- calling
# Vec2.__eq__(v, "x") would run `self.x == other.x` and raise AttributeError
# on `"x".x` instead. This is TPy's existing static-typing divergence from
# CPython's duck-typed dispatch (already true for non-exposed code) surfacing
# through a clean operator-protocol result instead of an AttributeError deep
# in the method body -- not a new divergence introduced by this feature.
import class_dunders

v = class_dunders.Vec2(1, 2)

print(v == "not a vec")      # False (NotImplemented on both sides -> identity fallback)
print(v != "not a vec")      # True

try:
    v < "not a vec"           # no fallback for ordering ops
    raise AssertionError("expected TypeError on cross-type ordering")
except TypeError:
    pass

# Ordered defines only __lt__ (no __eq__) -- in plain Python this stays
# hashable (only __eq__ nulls the default identity hash at the class-
# statement level). The compiled type does NOT stay hashable: PyType_Ready
# nulls tp_hash whenever tp_richcompare is populated at all, with no way to
# tell which specific comparison dunder populated it (that introspection is
# class-statement-specific, not available to a C-built heap type). This is
# an unavoidable, acknowledged divergence.
o = class_dunders.Ordered(1)
try:
    hash(o)
    raise AssertionError("expected TypeError: unhashable")
except TypeError:
    pass

print("ext-only class-dunder checks: PASS")
