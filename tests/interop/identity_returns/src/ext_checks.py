# Ext-only: the copying residue and the collision guard (documented in
# docs/CPYTHON_INTEROP.md; plain Python aliases everywhere).
import identity_returns as m

# A field-borrow return has no live PyObject behind it, so it copies: a new
# object each call, mutations invisible on the holder's payload.
h = m.Holder(9)
inner = h.get_inner()
assert inner is not h.get_inner()
inner.x = 99
assert h.get_inner().x == 9

# First-field address collision guard: Holder's payload STARTS with `_inner`,
# so &holder_payload == &inner_field numerically. Holder is unrelated to
# Inner, so the glue must not address-match `self` -- a naive compare would
# hand back the Holder object itself as the "returned Inner".
assert type(inner) is m.Inner
assert inner is not h

# Mixed body: the param arm aliases (asserted in driver.py), the field arm
# still copies.
i = m.Inner(7)
assert h.pick_inner(i, True) is not h.pick_inner(i, True)

# A module-global return has no candidates at all (free fn, no class
# params): a fresh copy each call.
assert m.get_default() is not m.get_default()

print("ext-only identity checks: PASS")
