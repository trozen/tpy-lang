# Shared across ext-exec and cpy-parity: read-only iteration values only.
# The boundary COPIES each yielded borrow (warned at compile), so aliasing
# diverges from plain Python -- that half is pinned ext-only in
# ext_checks.py; here every assertion is copy/alias-blind by design.
import next_borrow as m

print([n.v for n in m.Repeat(7)])
print([row for row in m.Rows()])
print([n.v for n in m.Fresh()])
print(next(iter(m.Repeat(1))).v)
