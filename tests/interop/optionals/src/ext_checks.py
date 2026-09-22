# Ext-only: a wrong type still raises TypeError through the gate (only None
# is the gate; a present value takes T's strict conversion), and the copy
# residue -- a value class and a container cross by copy, a module global
# has no live object behind it -- diverges from the aliasing source.
import optionals as m


def expect(exc, fn):
    try:
        fn()
    except exc:
        return "ok"
    return "no exception"


assert expect(TypeError, lambda: m.opt_int("x")) == "ok"
assert expect(TypeError, lambda: m.opt_str(3)) == "ok"
assert expect(TypeError, lambda: m.bump(3)) == "ok"
assert expect(TypeError, lambda: m.opt_list("abc")) == "ok"
assert expect(TypeError, lambda: m.opt_color(7)) == "ok"

xs = [1, 2]
out = m.opt_list(xs)
assert xs == [1, 2] and out == [1, 2, 2]
s = {1, 2}
assert m.opt_set(s) == 3 and s == {1, 2}

v = m.Vec2(1, 1)
assert m.opt_vec(v) is not v
h = m.Holder(1)
assert expect(TypeError, lambda: setattr(h, "note", 3)) == "ok"
assert expect(TypeError, lambda: setattr(h, "tint", "red")) == "ok"
# a value class behind the gate copies out on every read, like the bare
# value-class field (declared: the copy is read-only and never aliases)
h.anchor = m.Vec2(1, 2)
assert h.anchor is not h.anchor
assert expect(AttributeError, lambda: setattr(h.anchor, "x", 9)) == "ok"

assert m.origin_if(True) is not m.origin_if(True)
assert m.fresh(True) is not m.fresh(True)

print("ext-only optional checks: PASS")
