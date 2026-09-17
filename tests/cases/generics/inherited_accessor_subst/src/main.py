# Inherited generic accessors through a subclass of an instantiation
# (class Sub(Holder[int32, Rec])): @property getter/setter and __getitem__
# signatures must substitute K/V level-by-level (like fields/methods do), and
# reads must alias (mutation through the result is visible in the holder).
# A bare-T getter is spelled `val_or_ref_t<T>`, so whether it is a reference is
# decided at the INSTANTIATION -- the last two sections iterate one at a
# container argument, on both for-each routes.
from typing import Iterator

from tpy import int32


class Rec:
    x: int32

    def __init__(self, x: int32):
        self.x = x


class Holder[K, V]:
    _k: K
    _v: V

    def __init__(self, k: K, v: V):
        self._k = k
        self._v = v

    @property
    def key(self) -> K:
        return self._k

    @key.setter
    def key(self, value: K) -> None:
        self._k = value

    @property
    def val(self) -> V:
        return self._v

    def find(self, want: int32) -> V | None:
        if want > 0:
            return self._v
        return None


class Sub(Holder[int32, Rec]):
    pass


# free function: a bare-T getter at a CONTAINER argument returns `list<Rec>&`,
# so the loop aliases the holder's storage
def bump_all(h: Holder[int32, list[Rec]]) -> None:
    for r in h.val:
        r.x += 1


# generator: the same getter on the frame route, the loop body suspends
def bump_gen(h: Holder[int32, list[Rec]]) -> Iterator[int32]:
    for r in h.val:
        r.x += 10
        yield r.x


def main() -> None:
    h = Sub(5, Rec(7))
    print(h.key)          # inherited getter, K substituted to int32
    h.key = 6             # inherited setter, value param substituted
    print(h.key)
    h.val.x = 8           # aliasing: mutate through the inherited V getter
    print(h._v.x)
    r = h.find(1)         # inverse: plain method V | None keeps working
    if r is not None:
        print(r.x)
    d = Holder(1, Rec(9)) # inverse: direct instantiation unchanged
    print(d.val.x)
    g = Holder(1, [Rec(1), Rec(2)])
    bump_all(g)
    print("sync:", g._v[0].x, g._v[1].x)
    for n in bump_gen(g):
        print("frame:", n)
    print("owner:", g._v[0].x, g._v[1].x)


main()
