# Ext-only: where the compiled value-type exposure diverges from the plain
# mutable source class. The exposed value type is READ-ONLY (mutation raises,
# matching TPy immutability -- the source class is a plain mutable class), and a
# field read copies out (so the returned instance is not identity-stable). Both
# are kept out of the shared driver and pinned here against the built .so.
import value_typed_fields as m

p = m.Point(1, 2)

# A value type is immutable: its fields are read-only (no setter descriptor).
try:
    p.x = 5
    raise AssertionError("expected AttributeError setting a value-type field")
except AttributeError:
    pass

b = m.Box(m.Point(1, 2))

# Mutating through a value-type field loud-fails too (the copy-out is read-only),
# instead of the reference-class footgun of a silently-lost write.
try:
    b.origin.x = 5
    raise AssertionError("expected AttributeError mutating a value-type field")
except AttributeError:
    pass

# The holder's whole-value setter is strict-by-type: a non-Point value is
# rejected (instance_payload type-checks the incoming instance), where the
# untyped source class would just store it.
try:
    b.origin = 5
    raise AssertionError("expected TypeError setting origin to a non-Point")
except TypeError:
    pass

# Copy-out: each read yields a fresh (equal but not identical) instance.
assert b.origin is not b.origin
assert b.origin == m.Point(1, 2)      # unchanged by the rejected mutations

print("ext-only value-type-field checks: PASS")
