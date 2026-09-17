# `a, b = <chained lvalue>` where the tuple has a REFERENCE element: the
# source may be any field / subscript chain bottoming out in a declared name
# (`hs[0].pair`, `o.h.pair`, `self.o.h.pair`), not just one hop off the name.
# The whole chain lifts through `tuple_to_pointer` off the lvalue, so the
# element pointers come off the container and the unpacked element ALIASES it
# -- every mutating section writes through the alias and reads the change back
# through the chain, which a copy could not produce. The chain's ROOT decides
# the const spelling: the `readonly` and unmutated-`*args` sections take the
# same lift spelled `const Box*` and only read (the loop ones are the
# const-loop-var cell, whose binding is const because the ITERATION is; the
# nested one pins that a mutable borrow out of an inner loop var reaches the
# outer binding; the `pack_direct` pair pins that a pack answers the same one
# hop away and at the name itself; `ptr_ro_read` / `ptr_ro_loop`,
# `ro_field_loop` and `peek` are the const sources that live OUTSIDE the
# param-index verdicts -- a readonly POINTEE, a const name's field iterable
# and a `@readonly` method's receiver). The resumable twin is
# `generators/frame_unpack_ref_elem_lift`; a chain with a CALL in it stays
# rejected (`generators/error_gen_unpack_chained_recv`). An ACCESSOR hop -- a
# property getter, a user `__getitem__` -- is a hop only when the accessor
# hands back a C++ reference; the by-value siblings reject
# (`tuple/error_value_getitem_recv_unpack`).
from tpy import int32, Ptr, readonly


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    pair: tuple[int32, Box]
    label: tuple[str, int32]

    def __init__(self, n: int32) -> None:
        self.pair = (n, Box(n * 2))
        self.label = ("h", n)


class Outer:
    h: Holder

    def __init__(self, n: int32) -> None:
        self.h = Holder(n)


def subscript_field(hs: list[Holder]) -> None:
    # free function, subscript then field: two hops off the param
    a, b = hs[0].pair  # tpyc: ok
    b.n += 5
    print("subscript_field", a, hs[0].pair[1].n)


def field_field(o: Outer) -> None:
    # free function, field then field
    a, b = o.h.pair  # tpyc: ok
    b.n += 5
    print("field_field", a, o.h.pair[1].n)


def loop_var(os_: list[Outer]) -> None:
    # the chain roots at a LOOP VAR; the mutable borrow out of it is what
    # keeps the loop var's own binding non-const
    for o in os_:
        a, b = o.h.pair  # tpyc: ok
        b.n += 5
        print("loop_var", a, o.h.pair[1].n)


def nested_loop(grid: list[list[Outer]]) -> None:
    # the chain roots at the INNER loop var of a nested `for`: the mutable
    # borrow taken out of it has to reach the OUTER loop var's binding too,
    # or the outer one binds const and the lift is ill-formed
    for row in grid:
        for o in row:
            a, b = o.h.pair  # tpyc: ok
            b.n += 5
            print("nested_loop", a, o.h.pair[1].n)


def read_only(hs: readonly[list[Holder]]) -> None:
    # a READONLY root: the same lift, spelled `const Box*`, and the section
    # only reads -- the committed render is what pins the const half
    a, b = hs[0].pair  # tpyc: ok
    print("read_only", a, b.n)


def readonly_loop(hs: readonly[list[Holder]]) -> None:
    # a const LOOP VAR: iterating a readonly container binds `h` const
    # whatever the binding is spelled, so the lift off it must spell
    # `const Box*` too
    for h in hs:
        a, b = h.pair  # tpyc: ok
        print("readonly_loop", a, b.n)


class Store:
    items: list[Holder]

    def __init__(self, n: int32) -> None:
        self.items = [Holder(n)]

    def __getitem__(self, i: int32) -> Holder:
        return self.items[i]


class Owner:
    h: Holder

    def __init__(self, n: int32) -> None:
        self.h = Holder(n)

    @property
    def made(self) -> Holder:
        return self.h


def ref_getitem_hop(s: Store) -> None:
    # a user `__getitem__` hop: the accessor returns a reference, so the
    # element it hands back is storage the lift may point into
    a, b = s[0].pair  # tpyc: ok
    b.n += 5
    print("ref_getitem_hop", a, s[0].pair[1].n)


def ref_property_hop(o: Owner) -> None:
    # a @property hop, same rule: the getter returns a reference
    a, b = o.made.pair  # tpyc: ok
    b.n += 5
    print("ref_property_hop", a, o.made.pair[1].n)


def pack_read(*hs: Holder) -> None:
    # an UNMUTATED `*args` pack is spelled `varargs<const Holder>`; the lift
    # off its loop var has to spell `const Box*` to match, and the pack's
    # const-ness is the element flip, not a const-borrow param verdict
    for h in hs:
        a, b = h.pair  # tpyc: ok
        print("pack_read", a, b.n)


def pack_bump(*hs: Holder) -> None:
    # the mutating twin: the write through the unpacked element keeps the
    # pack's elements non-const, and the caller observes the change
    for h in hs:
        a, b = h.pair  # tpyc: ok
        b.n += 5
        print("pack_bump", a, b.n)


def pack_direct(*hs: Holder) -> None:
    # the pack read with NO loop var in between: the lift roots at the pack
    # NAME, so its const spelling has to come off the same element flip one
    # hop away -- a param-verdict-only answer spells `Box*` here and the
    # `varargs<const Holder>` element refuses it
    a, b = hs[0].pair  # tpyc: ok
    print("pack_direct", a, b.n)


def pack_direct_bump(*hs: Holder) -> None:
    # the mutating twin keeps `varargs<Holder>`, and the caller sees the write
    a, b = hs[0].pair  # tpyc: ok
    b.n += 5
    print("pack_direct_bump", a, b.n)


class Grid:
    rows: list[Holder]

    def __init__(self, n: int32) -> None:
        self.rows = [Holder(n)]


def ptr_root_bump(p: Ptr[Grid]) -> None:
    # the chain roots at a `Ptr[T]` param: the hop goes through the deref and
    # the elements stay mutable, so the write reaches the caller's grid
    a, b = p.rows[0].pair  # tpyc: ok
    b.n += 5
    print("ptr_root_bump", a, p.rows[0].pair[1].n)


def ptr_root_read(p: Ptr[Grid]) -> None:
    # the read-only twin of the same root
    a, b = p.rows[0].pair  # tpyc: ok
    print("ptr_root_read", a, b.n)


def ptr_ro_read(p: Ptr[readonly[Grid]]) -> None:
    # a `Ptr[readonly[T]]` root carries its const on the POINTEE, so no
    # top-level readonly and no param-index verdict says const -- the lift
    # still has to spell `const Box*` against the `const Grid*` receiver
    a, b = p.rows[0].pair  # tpyc: ok
    print("ptr_ro_read", a, b.n)


def ptr_ro_loop(p: Ptr[readonly[Grid]]) -> None:
    # the LOOP-VAR spelling off the same root: the iteration inherits the
    # pointee's const, so the loop var binds const and the lift off it does
    # too
    for h in p.rows:
        a, b = h.pair  # tpyc: ok
        print("ptr_ro_loop", a, b.n)


def ro_field_loop(g: readonly[Grid]) -> None:
    # the same question one family out: a one-hop FIELD iterable off a const
    # param name (not `self`) is admitted by the for-each route, so its
    # elements are const and the lift off the loop var must follow
    for h in g.rows:
        a, b = h.pair  # tpyc: ok
        print("ro_field_loop", a, b.n)


def value_elements(hs: list[Holder]) -> None:
    # the same widened source gate with NO reference element: there is
    # nothing to alias, so the chain is captured by value into a local tuple
    # and the `str` target views THAT copy, not the holder
    a, b = hs[0].label  # tpyc: ok
    print("value_elements", a, b)


class Keeper:
    o: Outer

    def __init__(self, n: int32) -> None:
        self.o = Outer(n)

    def bump(self) -> None:
        # method: a two-hop chain off the `self` receiver; the write keeps
        # the receiver non-const and the caller observes it through `self`
        a, b = self.o.h.pair  # tpyc: ok
        b.n += 5
        print("self_chain", a, self.o.h.pair[1].n)

    @readonly
    def peek(self) -> None:
        # the `@readonly` twin: the receiver's const lives on the METHOD, in
        # neither param-verdict set, so the lift off `self` spells
        # `const Box*` -- and the section only reads through it
        a, b = self.o.h.pair  # tpyc: ok
        print("self_chain_readonly", a, b.n)


def main() -> None:
    xs: list[Holder] = [Holder(1)]
    subscript_field(xs)
    print("subscript_field caller", xs[0].pair[1].n)

    o = Outer(3)
    field_field(o)
    print("field_field caller", o.h.pair[1].n)

    os_: list[Outer] = [Outer(5), Outer(7)]
    loop_var(os_)
    print("loop_var caller", os_[0].h.pair[1].n, os_[1].h.pair[1].n)

    grid: list[list[Outer]] = [[Outer(13)], [Outer(15)]]
    nested_loop(grid)
    print("nested_loop caller", grid[0][0].h.pair[1].n,
          grid[1][0].h.pair[1].n)

    ys: list[Holder] = [Holder(9)]
    read_only(ys)
    readonly_loop(ys)
    value_elements(ys)

    p1 = Holder(31)
    p2 = Holder(33)
    pack_read(p1, p2)
    pack_bump(p1, p2)
    print("pack caller", p1.pair[1].n, p2.pair[1].n)

    p3 = Holder(35)
    pack_direct(p3)
    pack_direct_bump(p3)
    print("pack_direct caller", p3.pair[1].n)

    gr = Grid(41)
    ptr_root_bump(gr)
    ptr_root_read(gr)
    print("ptr_root caller", gr.rows[0].pair[1].n)

    gro = Grid(43)
    ptr_ro_read(gro)
    ptr_ro_loop(gro)
    ro_field_loop(gro)

    st = Store(21)
    ref_getitem_hop(st)
    print("ref_getitem_hop caller", st[0].pair[1].n)

    ow = Owner(23)
    ref_property_hop(ow)
    print("ref_property_hop caller", ow.made.pair[1].n)

    k = Keeper(11)
    k.bump()
    print("self_chain caller", k.o.h.pair[1].n)
    k.peek()


main()
