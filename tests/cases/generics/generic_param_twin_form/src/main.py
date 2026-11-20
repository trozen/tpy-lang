# A generic `T` parameter takes the form the function written for the concrete
# type takes: at a by-value instantiation (`int32`, a ValueType record) it is
# the callee's own value, so a body that writes the caller's source (a field,
# a global) does not change it, as in CPython; at `int`, `String` and a tuple
# it stays `const&`, as the twin's (BUGS.md#borrowed-arg-into-mutated-storage);
# a class `T` still aliases. Positions in part_positions, copies and moves
# in part_moves, forwarders in part_shims, frames and member-inits in
# part_frames, reads after a candidate last use in part_reads and under a
# live view in part_lends, `Send[T]` / `readonly[T]` spellings in
# part_qualified.
from tpy import int32
from fixtures import Box, C, Text, V
import part_positions
import part_moves
import part_shims
import part_frames
import part_reads
import part_lends
import part_qualified

G = 1
GS = V("g0")


# free function, field face: the body writes the box the argument was read from
def after_field[T](x: T, b: Box, t: Text) -> T:
    b.n = 9
    t.s = "changed"
    return x  # tpyc: ok


# free function, global face: the body rebinds the global the argument read
def after_global[T](x: T) -> T:
    global G, GS
    G = 7
    GS = V("g1")
    return x


def show_int(tag: str, x: int32) -> None:
    print(tag, x)


def field_face() -> None:
    b = Box(1)
    t = Text("orig")
    # int32: the parameter is the value read before the body wrote the field
    show_int("field int32", after_field(b.n, b, t))
    # A `str` argument viewing the field the body replaces is the view case
    # (BUGS.md#generic-body-param-view-dangles-unwarned), not a section here.
    t.s = "orig"
    # a ValueType holding a str
    v = V("v0")
    print("field V", after_field(v, b, t).s)
    # instantiations only, from a local and a temporary: no field is passed
    big: int = 10 ** 20
    print("inst int", after_field(big, b, t))
    b.n = 2
    t.s = "orig"
    print("inst tuple", after_field((b.n, t.s), b, t))


def global_face() -> None:
    global G, GS
    show_int("global int32", after_global(G))
    G = 1
    GS = V("g0")
    print("global V", after_global(GS).s, G)
    print("global after", G, GS.s)


# a class `T` aliases, as the function written for the class does
def bump[T](x: T, c: C) -> T:
    c.n += 1
    return x


def class_aliases() -> None:
    c = C(1)
    r = bump(c, c)
    print("class alias", r.n, r is c)
    c.n = 50
    print("class alias after", r.n)


def main() -> None:
    field_face()
    global_face()
    class_aliases()
    part_positions.main()
    part_moves.main()
    part_shims.main()
    part_frames.main()
    part_reads.main()
    part_lends.main()
    part_qualified.main()


main()
