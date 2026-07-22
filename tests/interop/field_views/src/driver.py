# Shared across ext-exec and cpy-parity: a never-reassigned class-typed
# field behaves like a plain Python attribute -- the same object comes back
# from the attribute, the accessor method (plain and readonly), the
# property, the tp_iter slot, and a free function reading a param's field
# (one view via the registry); mutations write through; the offset-0 first
# field is its own object, not the holder, and nested first fields keep
# their own identities per type.
import field_views as m

p = m.Pack(1, 2)
a = p.first
print(type(a).__name__, a.n, p.second.n)
print(p.first is a, p.get_first() is a, p.head is a)
print(p.peek_first() is a, m.first_of(p) is a, iter(p) is a)
print(p.first == a, hash(p.first) == hash(a))
a.n = 42
print(p.first.n, p.get_first().n)
print(p.first is not p, p.second is not p.first)

b = p.second
b.n += 1
print(p.second.n)

# The view is a live iterator over shared state (Cell.__next__ counts down).
p.first.n = 2
it = iter(p)
print(next(it), next(a), a.n)

# Base-declared fields through a derived instance.
ps = m.PackSub(7, 8)
print(ps.first is ps.get_first(), ps.head.n)

# Nested first fields: Wrap's payload starts at its Pack, whose payload
# starts at its first Cell -- three types at one address, each with its own
# correctly-typed view.
w = m.Wrap()
pk = w.get_pack()
print(type(pk).__name__, pk is w.get_pack(), pk is not w)
c = pk.first
print(type(c).__name__, c is pk.first, c is not pk, c is not w)
c.n = 99
print(w.get_pack().first.n)

# Keepalive: the view outlives the holder's last direct reference.
k = m.Pack(7, 8).first
k.n = 9
print(k.n)
