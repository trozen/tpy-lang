# Inherited generic accessors through a subclass of an instantiation
# (class Sub(Holder[Int32, Rec])): @property getter/setter and __getitem__
# signatures must substitute K/V level-by-level (like fields/methods do), and
# reads must alias (mutation through the result is visible in the holder).
from tpy import Int32


class Rec:
    x: Int32

    def __init__(self, x: Int32):
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

    def find(self, want: Int32) -> V | None:
        if want > 0:
            return self._v
        return None


class Sub(Holder[Int32, Rec]):
    pass


def main() -> None:
    h = Sub(5, Rec(7))
    print(h.key)          # inherited getter, K substituted to Int32
    h.key = 6             # inherited setter, value param substituted
    print(h.key)
    h.val.x = 8           # aliasing: mutate through the inherited V getter
    print(h._v.x)
    r = h.find(1)         # inverse: plain method V | None keeps working
    if r is not None:
        print(r.x)
    d = Holder(1, Rec(9)) # inverse: direct instantiation unchanged
    print(d.val.x)


main()
