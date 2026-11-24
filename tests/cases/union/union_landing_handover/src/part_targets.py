# Iteration and handler targets returned at a union: a loop-head unpack
# target's own copy moves, a reference one copies, as does an `except`
# target and a generator's yield while an iterator over it is suspended.
from typing import Iterator
from tpy import Own, ValueType, int32
from part_subclass import VBase, VSub, show_v
from part_values import RBase, RSub
from part_handover import VIt, first

L = "a-string-long-enough-to-own-a-heap-buffer-xxxxx"


# the target is its own copy of the element (`VSub v = std::get<0>(..)`):
# moved, the element keeping its value
def loop_unpack(xs: list[tuple[VSub, int32]]) -> VBase | int32:
    for v, n in xs:
        return v  # tpyc: warning(/narrows 'VSub'/)
    return 0


def loop_unpack_zip(xs: list[VSub], ns: list[int32]) -> VBase | int32:
    for v, n in zip(xs, ns):
        return v  # tpyc: warning(/narrows 'VSub'/)
    return 0


# the target refers to the element (`const auto& r = std::get<0>(..)`):
# copied
def loop_unpack_ref(xs: list[tuple[RSub, int32]]) -> Own[RBase | int32]:
    for r, n in xs:
        return r  # tpyc: warning(/narrows 'RSub'/) warning(/copies RSub/)
    return 0


def loops() -> None:
    xs = [(VSub(L), 1)]
    show_v("targets-loop-unpack", loop_unpack(xs))
    print("targets-loop-unpack-kept", xs[0][0].s)
    ys = [VSub(L)]
    show_v("targets-loop-unpack-zip", loop_unpack_zip(ys, [1]))
    print("targets-loop-unpack-zip-kept", ys[0].s)
    rs = [(RSub(), 1)]
    u = loop_unpack_ref(rs)
    # the element keeps its items (a move would leave it empty); the result
    # is the warned copy, so only the element's side is printed
    for r, n in rs:
        r.items.append(9)
        print("targets-loop-unpack-ref", isinstance(u, RBase), len(r.items))


class EBase(Exception):
    s: str

    def __init__(self, s: str) -> None:  # tpyc: warning(/does not call 'super/)
        self.s = s


class ESub(EBase):
    def __init__(self, s: str) -> None:  # tpyc: warning(/does not call 'super/)
        self.s = s


# an `except` target is the caught object: copied
def except_target() -> Own[EBase | int32]:
    try:
        raise ESub(L)
    except ESub as e:
        return e  # tpyc: warning(/narrows 'ESub'/) warning(/copies ESub/)
    return 0


class VItSub(VIt, ValueType):
    def __init__(self, s: str) -> None:  # tpyc: warning(/does not call 'super/)
        self.s = s


# a by-value parameter yielded at the union while an iterator over it is
# suspended is copied: the iterator reads the whole string after the yield
def gen_union(v: VItSub) -> Iterator[VIt | int32]:
    it = v.items()
    yield v  # tpyc: warning(/narrows 'VItSub'/)
    print("targets-gen-union-borrower", first(it))


def main() -> None:
    loops()
    u = except_target()
    if isinstance(u, EBase):
        print("targets-except", u.s)
    for g in gen_union(VItSub(L)):
        if isinstance(g, VIt):
            print("targets-gen-union", g.s)
