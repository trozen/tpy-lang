# Arguments at an owning element slot (`Iterable[Own[R]]`): a container the
# caller gives up is handed over (moved into its own consuming iterator); a lent
# one is copied element by element, warned; an iterator object passes as
# itself. Each part covers one callee or slot kind. A lent section's copy is
# the warned difference from CPython (which shares the elements), so it
# prints only what both agree on: the callee's elements, mutated.
from typing import Iterable, Iterator
from tpy import Own, int32, copy_iter, own_iter

import part_callees
import part_frames
import part_builtins
import part_slots
import part_nocopy
import part_bindings
import part_handover
import part_walrus
import part_adapters


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    rs: list[R]

    def __init__(self) -> None:
        self.rs = [R(1), R(2)]


def take(xs: Iterable[Own[R]]) -> Own[list[R]]:
    out: list[R] = []
    for x in xs:
        out.append(x)
    return out


def mk() -> Own[list[R]]:
    return [R(1), R(2)]


def fwd(xs: Iterable[Own[R]]) -> Own[list[R]]:
    # An owning parameter passed on keeps the category it arrived in.
    return take(xs)  # tpyc: ok


def lend(xs: list[R]) -> Iterator[R]:
    for x in xs:
        yield x


def shapes() -> None:
    d = [R(1), R(2)]
    out = take(d)  # tpyc: ok
    out[0].v = 9
    print("dead", out[0].v)
    live = [R(1), R(2)]
    out = take(live)  # tpyc: warning(/copies R elements/)
    out[0].v = 9
    print("live", out[0].v, len(live))
    ll = [[R(1)], [R(2)]]
    out = take(ll[0])  # tpyc: warning(/copies R elements/)
    out[0].v = 9
    print("element", out[0].v, len(ll[0]))
    h = H()
    out = take(h.rs)  # tpyc: warning(/copies R elements/)
    out[0].v = 9
    print("field", out[0].v, len(h.rs))
    print("literal", len(take([R(1), R(2)])))  # tpyc: ok
    print("fresh", len(take(mk())))  # tpyc: ok
    print("comprehension", len(take([R(i) for i in range(3)])))  # tpyc: ok
    src = [R(1), R(2)]
    out = take(x for x in src)  # tpyc: warning(/copies R elements/)
    out[0].v = 9
    print("genexpr", out[0].v, len(src))
    src2 = [R(1), R(2)]
    out = take(iter(src2))  # tpyc: warning(/copies R elements/)
    out[0].v = 9
    print("iter", out[0].v, len(src2))
    src3 = [R(1), R(2)]
    out = take(copy_iter(src3))  # tpyc: ok
    out[0].v = 9
    print("copy_iter", src3[0].v)
    src4: list[R] = [R(1), R(2)]
    out = take(own_iter(src4))  # tpyc: ok
    print("own_iter", len(out))
    o: list[R] | None = [R(1)]
    if o is not None:
        out = take(o)  # tpyc: warning(/copies R elements/)
        out[0].v = 9
        print("narrowed", out[0].v, len(o))
    print("forwarded", len(fwd(mk())))
    src5 = [R(1), R(2)]
    out = take(lend(src5))  # tpyc: warning(/copies R elements/)
    out[0].v = 9
    print("generator", out[0].v, len(src5))


def main() -> None:
    shapes()
    part_callees.main()
    part_frames.main()
    part_builtins.main()
    part_slots.main()
    part_nocopy.main()
    part_bindings.main()
    part_handover.main()
    part_walrus.main()
    part_adapters.main()


main()
