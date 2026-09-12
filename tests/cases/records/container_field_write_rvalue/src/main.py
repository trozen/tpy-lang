# An Array / bytearray FIELD takes the same rvalue and borrow-call sources a
# list / dict / set field already took: a converting construction, an owning
# call, a repeat literal, and a borrow-returning call (which copies, warned).
from tpy import Array, int32, Own


def mk_arr() -> Own[Array[int32, 4]]:
    return [7, 7, 7, 7]


def mk_ba() -> Own[bytearray]:
    return bytearray(b"xy")


def borrow_arr(x: Array[int32, 4]) -> Array[int32, 4]:
    return x


class Buf:
    n: int32
    a: Array[int32, 4]
    ba: bytearray

    def __init__(self, k: int32) -> None:
        self.n = k
        # The raise keeps the field writes in the ctor BODY rather than the
        # member-init list, whose nominal-call row still rejects them.
        if k < 0:
            raise ValueError("neg")
        self.a = Array[int32, 4]()
        self.ba = bytearray()

    def load(self, src: bytes) -> None:
        # The converting construction: `this->ba = ::tpy::ByteArray(src);`
        self.ba = bytearray(src)

    def own_call(self) -> None:
        self.a = mk_arr()
        self.ba = mk_ba()

    def repeat(self) -> None:
        self.a = [3] * 4

    def alias(self, x: Array[int32, 4]) -> None:
        # A borrow-returning call COPIES into the field; sema warns it, so the
        # copy is declared rather than silent. The caller does not observe the
        # copy-vs-alias split, which CPython would resolve the other way.
        self.a = borrow_arr(x)  # tpyc: warning(/copies Array\[int32, 4\] into field/)


def main() -> None:
    b = Buf(1)
    b.load(b"abc")
    print(len(b.ba), b.a[0])
    b.own_call()
    print(len(b.ba), b.a[0])
    # The field is the record's own storage, so mutating it after the write is
    # visible on the next read through the same record.
    b.ba.append(33)
    print(len(b.ba))
    b.repeat()
    print(b.a[0])
    q: Array[int32, 4] = [5, 5, 5, 5]
    b.alias(q)
    print(b.a[0])


main()
