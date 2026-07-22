# Ext-only: the remaining copy residue and the view re-init guard
# (documented in docs/CPYTHON_INTEROP.md; field borrows now alias via
# borrow views and are parity-asserted in driver.py).
import identity_returns as m

# A module-global return has no candidates at all (free fn, no class
# params): a fresh copy each call.
assert m.get_default() is not m.get_default()

# Explicit __init__ on a borrow view is rejected loudly (its storage
# belongs to the holder); plain Python would just rebind attributes.
h = m.Holder(9)
inner = h.get_inner()
try:
    inner.__init__(1)
    raise AssertionError("expected TypeError re-initializing a view")
except TypeError:
    pass

print("ext-only identity checks: PASS")
