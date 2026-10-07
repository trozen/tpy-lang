# An element read off a tuple LOCAL behaves like the same read off a tuple
# parameter: an alias (`a = t[0]`) and an unpack (`x, k = t`) reach the same
# object whatever bound the tuple -- a literal, a call, a container element,
# a field, a loop variable, an `Own[tuple]` parameter -- and a write through
# the alias is visible on the original, as in CPython.
from tpy import int32, Own, nocopy, readonly


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


@nocopy
class Counter:
    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    t: tuple[Box, int32]

    def __init__(self, b: Box) -> None:
        self.t = (b, 1)  # tpyc: warning(/copies Box into field/)


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


def make_pair(b: Box) -> tuple[Box, int32]:
    return (b, 2)


# free function: a literal-bound local, unpacked and element-aliased
def literal_local(b: Box) -> None:
    t = (b, 1)
    x, k = t  # tpyc: ok
    x.n += k
    a = t[0]  # tpyc: ok
    a.n += 10
    u = t  # tpyc: ok
    u[0].n += 100


# free function: a MIXED literal local -- the fresh element is held inline
# beside the borrowed one, and every read aliases the right storage
def literal_mixed(b: Box) -> int32:
    t = (Box(1), b)
    a = t[0]  # tpyc: ok
    a.n += 10
    y = t[1]  # tpyc: ok
    y.n += 20
    u = t  # tpyc: ok
    u[0].n += 100
    x, z = t  # tpyc: ok
    z.n += 200
    return x.n + t[0].n


# free function: the mixed literal beside an OPTIONAL element -- the inline
# element is still known inline, so `u = t` aliases and `a = t[0]` binds it
def literal_mixed_optional(p: Box | None) -> int32:
    if p is not None:
        p.n = 2
    t = (Box(1), p)
    u = t  # tpyc: ok
    x, _ = u  # tpyc: ok
    x.n = 9
    a = t[0]  # tpyc: ok
    a.n += 30
    y, _ = t  # tpyc: ok
    return y.n


# free function: an element alias off a const-inferred container alias
# binds `const Box&`
def const_alias(xs: list[tuple[Box, int32]]) -> int32:
    t = xs[0]  # tpyc: ok
    a = t[0]  # tpyc: ok
    return a.n


# free function: a readonly tuple's Optional element unpacks to a const
# pointer
def readonly_optional_unpack(p: readonly[tuple[Box | None, int32]]) -> int32:
    t = p  # tpyc: ok
    a, k = t  # tpyc: ok
    if a is not None:
        return a.n + k
    return k


# free function: a TERNARY of inline-holding names aliases like the name
def literal_mixed_ternary(b: Box, flag: bool) -> int32:
    t = (Box(1), b)
    u = t if flag else t  # tpyc: ok
    x, _ = u  # tpyc: ok
    x.n = 9
    y, _ = t
    return y.n


# comprehension: its loop variable shadows an inline-holding local and reads
# the container's storage; the outer local is intact after it
def shadowed_in_comprehension(b: Box, rows: list[tuple[int32, int32, Box]]) -> int32:
    b.n += 1
    t = (Box(1), b)
    values = [t[2].n for t in rows]  # tpyc: ok
    return values[0] + t[0].n


# free function: an alias of a const-inferred tuple parameter binds its
# element `const Box&`
def param_alias(pair: tuple[Box, int32]) -> int32:
    saved = pair  # tpyc: ok
    a = saved[0]  # tpyc: ok
    return a.n


# nested def: its parameter shadows an outer reassigned tuple local whose
# sources are const, and stays mutable
def nested_shadow(rows: list[tuple[Box, int32]], b: Box) -> int32:
    t = rows[0]
    t = rows[1]
    def mutate(t: Box) -> None:
        a = t  # tpyc: ok
        a.n = 9
    mutate(b)
    return b.n + t[0].n


# free function: a call-bound local (borrow result)
def call_local(b: Box) -> None:
    t = make_pair(b)
    x, k = t  # tpyc: ok
    x.n += k
    a = t[0]  # tpyc: ok
    a.n += 10


# free function: a MIXED call-bound local; the borrowed half aliases, the
# owned half moves out at the unpack
def mixed_local(b: Box) -> int32:
    t = make_mixed(b)
    y = t[1]  # tpyc: ok
    y.n += 5
    o, z = t  # tpyc: ok
    z.n += 50
    return o.n


# free function: a local aliasing a container element, off a parameter list
def elem_of_param_list(xs: list[tuple[Box, int32]]) -> None:
    t = xs[0]  # tpyc: ok
    a = t[0]  # tpyc: ok
    a.n += t[1]
    x, k = t  # tpyc: ok
    x.n += k


# free function: a local aliasing a tuple field
def field_alias(h: H) -> None:
    t = h.t  # tpyc: ok
    a = t[0]  # tpyc: ok
    a.n += 7


# loop body: the loop variable's element aliased in the body
def loop_var(xs: list[tuple[Box, int32]]) -> None:
    for t in xs:
        a = t[0]  # tpyc: ok
        a.n += 1000


# free function: a READONLY list's tuple element -- the alias, the unpack
# target and the direct element read are readonly views of the same object
def readonly_elems(items: readonly[list[tuple[Box, int32]]]) -> int32:
    t = items[0]  # tpyc: ok
    a = t[0]  # tpyc: ok
    b, k = items[0]  # tpyc: ok
    return a.n + b.n + k + items[0][0].n


# method: an `Own[tuple]` parameter's element aliased (storage form, no copy
# of the @nocopy element)
class Sink:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    def take(self, p: Own[tuple[int32, Counter]]) -> None:
        c = p[1]  # tpyc: ok
        c.n += p[0]
        self.total += c.n


def main() -> None:
    xs = [(Box(0), 3)]
    b = Box(0)
    literal_local(b)
    print("literal_local", b.n)
    b = Box(0)
    print("literal_mixed", literal_mixed(b), b.n)
    print("literal_mixed_optional", literal_mixed_optional(Box(0)))
    print("const_alias", const_alias(xs))
    print("literal_mixed_ternary", literal_mixed_ternary(Box(0), True))
    print("shadowed_in_comprehension", shadowed_in_comprehension(Box(0), [(0, 0, Box(7))]))
    print("param_alias", param_alias((Box(7), 0)))
    print("nested_shadow", nested_shadow([(Box(1), 0), (Box(2), 0)], Box(0)))
    print("readonly_optional_unpack", readonly_optional_unpack((Box(4), 1)))
    b = Box(0)
    call_local(b)
    print("call_local", b.n)
    b = Box(0)
    print("mixed_local", mixed_local(b), b.n)
    elem_of_param_list(xs)
    print("elem_of_param_list", xs[0][0].n)
    h = H(Box(0))
    field_alias(h)
    print("field_alias", h.t[0].n)
    loop_var(xs)
    print("loop_var", xs[0][0].n)
    print("readonly_elems", readonly_elems(xs))
    s = Sink()
    s.take((4, Counter(2)))
    print("own_param", s.total)


main()
