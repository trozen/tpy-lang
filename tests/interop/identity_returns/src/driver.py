# Shared across ext-exec and cpy-parity: identity-preserved returns behave
# exactly like plain Python -- the same object comes back, mutations through
# it are visible on the original. The copying residue (field-borrow return)
# is only value-read here; its identity divergence lives in ext_checks.py.
import identity_returns as m

b = m.Box(5)
print(b.me() is b)
print(m.identity(b) is b)
print(b.itself is b)
print(b.view() is b)
print(m.get_default().v)

c = b.bump().bump()
print(c is b, b.v)

o = m.Box(1)
print(b.pick(o, True) is b)
p = b.pick(o, False)
print(p is o)
p.v = 42
print(o.v)

print(b.pick(b, False) is b)

f = m.fresh(3)
print(f is b, f.v)

h = m.Holder(9)
print(h.get_inner().x)
i = m.Inner(7)
print(h.pick_inner(i, False) is i)
