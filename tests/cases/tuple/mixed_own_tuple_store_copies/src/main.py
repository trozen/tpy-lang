# Storing a mixed owned+borrow tuple (`tuple[Own[Box], Box]`) into an owning
# slot COPIES its borrowed element, so a write through the sink is NOT seen on
# the original. Every sink mutates then reads the original to observe it -- the
# sibling `mixed_own_element_read` covers the same sinks read-only to stay
# cpy-comparable, so this is the case that actually pins copy-vs-alias.
#
# The copy is an acknowledged divergence: CPython aliases (each function here
# would print its written value), TPy warns at every store and offers copy() as
# the acknowledgement. That is why this case is no_cpython -- the divergence is
# the thing under test, not an oversight.
from tpy import Int32, Own


class Box:
    val: Int32

    def __init__(self, val: Int32) -> None:
        self.val = val


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


def via_list_literal(b: Box) -> Int32:
    xs = [make_mixed(b)]  # tpyc: warning(/copies Box into owned storage/)
    xs[0][1].val = 21
    return b.val


def via_append(b: Box) -> Int32:
    xs: list[tuple[Box, Box]] = []
    xs.append(make_mixed(b))  # tpyc: warning(/copies Box into owned storage/)
    xs[0][1].val = 22
    return b.val


def via_dict_literal(b: Box) -> Int32:
    d = {1: make_mixed(b)}  # tpyc: warning(/copies Box into owned storage/)
    d[1][1].val = 23
    return b.val


def via_setitem(b: Box) -> Int32:
    d: dict[Int32, tuple[Box, Box]] = {}
    d[1] = make_mixed(b)  # tpyc: warning(/copies Box into container/)
    d[1][1].val = 24
    return b.val


def via_nested_tuple(b: Box) -> Int32:
    # UNWARNED, and the one sink here that is: the per-member check inspects
    # direct members only, and this member is itself a value tuple, so its
    # borrowed element sits a level too deep to be seen. Tracked in BUGS.md as
    # the nested-depth limit -- the copy below still happens, which is why this
    # sink is worth observing even without the diagnostic.
    q = (make_mixed(b), 1)  # tpyc: ok
    q[0][1].val = 25
    return b.val


def via_dict_comprehension(b: Box) -> Int32:
    d = {i: make_mixed(b) for i in range(1)}  # tpyc: warning(/copies Box into owned storage/)
    d[0][1].val = 29
    return b.val


def via_ternary_source(b: Box, c: Box, flag: bool) -> Int32:
    # C++ evaluates one arm, so the ternary carries a borrow iff both arms do --
    # and the warning has to compose the same way the copy does, or the store is
    # silent.
    xs = [make_mixed(b) if flag else make_mixed(c)]  # tpyc: warning(/copies Box into owned storage/)
    xs[0][1].val = 30
    return b.val


def via_comprehension(b: Box) -> Int32:
    xs = [make_mixed(b) for _ in range(1)]  # tpyc: warning(/copies Box into owned storage/)
    xs[0][1].val = 26
    return b.val


def via_loop_var(b: Box) -> Int32:
    xs = [make_mixed(b)]  # tpyc: warning(/copies Box into owned storage/)
    for t in xs:
        t[1].val = 27
    return b.val


class Holder:
    t: tuple[Box, Box]

    def __init__(self, b: Box) -> None:
        self.t = make_mixed(b)  # tpyc: warning(/copies Box into field/)


def via_field(b: Box) -> Int32:
    h = Holder(b)
    h.t[1].val = 28
    return b.val


def main() -> None:
    # Every one prints 2 -- the write landed on the copy, not on `b`.
    print(via_list_literal(Box(2)), via_append(Box(2)))
    print(via_dict_literal(Box(2)), via_setitem(Box(2)))
    print(via_nested_tuple(Box(2)), via_comprehension(Box(2)))
    print(via_loop_var(Box(2)), via_field(Box(2)))
    print(via_dict_comprehension(Box(2)), via_ternary_source(Box(2), Box(3), True))


main()
