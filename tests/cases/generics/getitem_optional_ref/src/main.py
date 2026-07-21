# __getitem__ returning `V | None` (reference V) is a method CALL returning
# borrow-form V*, not a storage container-element read: the subscript must not
# be lifted via optional_to_ptr, on a concrete class, a generic instantiation,
# a subclass of one, and through a readonly receiver (the implicit const twin
# also needs the `&` lift on its return). Hits alias: mutation through the
# result is visible in the holder. Misses yield None.
from tpy import Int32, readonly


class Rec:
    x: Int32

    def __init__(self, x: Int32):
        self.x = x


class Box:
    _v: Rec

    def __init__(self, v: Rec):
        self._v = v

    def __getitem__(self, want: Int32) -> Rec | None:
        if want > 0:
            return self._v
        return None


class GenBox[K, V]:
    _k: K
    _v: V

    def __init__(self, k: K, v: V):
        self._k = k
        self._v = v

    def __getitem__(self, want: Int32) -> V | None:
        if want > 0:
            return self._v
        return None


class SubBox(GenBox[Int32, Rec]):
    pass


def peek(b: readonly[Box]) -> None:
    p = b[1]
    if p is not None:
        print(p.x)


def main() -> None:
    b = Box(Rec(7))
    p = b[1]
    if p is not None:
        p.x = 8           # aliasing: mutate through the subscript result
    print(b._v.x)
    m = b[0]              # miss arm
    if m is None:
        print("miss")
    peek(b)               # const twin through a readonly receiver

    g = GenBox(1, Rec(20))
    q = g[1]
    if q is not None:
        print(q.x)

    s = SubBox(2, Rec(30))
    r = s[1]
    if r is not None:
        r.x = 31
    print(s._v.x)


main()
