# Shared across ext-exec and cpy-parity: None crosses as None in both
# directions, a present value as itself; identity and write-through hold for
# the reference form exactly as without the Optional.
import optionals as m

print(m.opt_int(None), m.opt_int(41))
print(m.opt_str(None), m.opt_str("hi"))
print(m.opt_bytes(None), m.opt_bytes(b"ab"))
print(m.opt_color(None), m.opt_color(m.Color.RED), m.opt_color(m.Color.BLUE))
v = m.opt_vec(m.Vec2(1, 2))
print(m.opt_vec(None), v.x, v.y)

p = m.Point(5)
print(m.bump(None))
r = m.bump(p)
print(r is p, p.x)
print(m.bump(p, 10) is p, p.x)
print(m.bump(p, by=None).x)
print(m.x_or(), m.x_or(p), m.x_or(None, 7), m.x_or(fallback=9))
print(m.scaled(2), m.scaled(2, None), m.scaled(2, 5, None), m.scaled(2, tag="t"))

print(m.pair_or(), m.pair_or(None), m.pair_or((m.Color.BLUE, 4)))
print(m.opt_list(None), m.opt_list([7, 8]))
print(m.count_or(), m.count_or(None), m.count_or([1, 2, 3]))
print(m.opt_set(None), m.opt_set({1, 2}))
print(m.opt_dict(None), m.opt_dict({"a": 1, "b": 2}))
print(m.opt_pair(None), m.opt_pair((1, "ab")))
print(m.never(p))

h = m.Holder(3)
print(h.inner_if(False))
i = h.inner_if(True)
i.v = 30
print(h.inner_if(True) is i, h.inner_if(True).v)
o = m.Holder(4)
print(h.pick(None, True) is h, h.pick(o, False) is o, h.pick(None, False))
print(h.has_label(), m.Holder(1, "tag").has_label(), m.Holder(1, label=None).has_label())
print(h.seed, m.Holder(1, seed=p).seed, p.x, m.Holder(1, None, None).seed)
print(h.limit)
h.limit = 12
print(h.limit)
h.limit = None
print(h.limit)
print(h.note, h.tint, h.anchor)
h.note = "n"
h.tint = m.Color.BLUE
h.anchor = m.Vec2(3, 4)
print(h.note, h.tint, h.anchor.x, h.anchor.y)
h.note = None
h.tint = None
h.anchor = None
print(h.note, h.tint, h.anchor)

print(m.fresh(False), m.fresh(True).x)
print(m.mint(False), m.mint(True).id)
print(m.origin_if(False), m.origin_if(True).x)
print(m.origin_ro(False), m.origin_ro(True).x)
