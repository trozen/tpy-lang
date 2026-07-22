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

# A never-reassigned field borrow crosses as an aliasing borrow view:
# repeated accesses are the SAME object (so eq/hash hold), mutation writes
# through, the mixed body's field arm aliases too, and the view of h's
# first field (which sits at h's own payload address) is a real Inner --
# never h itself.
inner = h.get_inner()
print(inner is h.get_inner(), inner == h.get_inner())
inner.x = 99
print(h.get_inner().x)
print(h.pick_inner(i, True) is inner)
print(type(inner) is m.Inner, inner is not h)

# Keepalive: a view outlives its holder's last direct reference.
v = m.Holder(5).get_inner()
v.x = 77
print(v.x)

# The return-self iterator: iter() is the SAME object, so cursor state is
# shared (interleaved next on the iterator and the original advance one
# cursor) and exhaustion sticks -- a copied iterator would restart.
cur = m.Cursor(4)
print(iter(cur) is cur)
it = iter(cur)
print(next(it), next(cur), next(it))
print(list(cur))
print(list(cur))

# Inherited __iter__ (base slot on a derived instance): identity holds and
# the dynamic type survives un-sliced.
sub = m.CursorSub(2)
print(iter(sub) is sub, type(iter(sub)) is m.CursorSub)
print(list(sub), list(sub))
