# repr() / `!r` / str() of bytes, bytearray and range: each type's stub
# declares `__repr__`, so it is Representable (and Stringable via __repr__).
from typing import Iterator

from tpy import int8, int32, int64, uint8


def repr_param(b: bytes) -> str:
    # repr() of a bytes parameter, which is a view in C++.
    return repr(b)  # tpyc: ok


def section_bytes() -> None:
    # bytes local: printable, non-printable and quote-choosing contents.
    b = b"a\x03"
    print("bytes:", repr(b))  # tpyc: ok
    print("bytes:", repr(b"it's"))  # tpyc: ok
    print("bytes:", repr(b'say "hi"'))  # tpyc: ok
    print("bytes:", repr(b"it's \"both\""))  # tpyc: ok
    print("bytes:", repr(b"\t\n\\\xff"))  # tpyc: ok
    print("bytes:", repr(bytes([0, 65, 127])))  # tpyc: ok
    print("bytes:", repr_param(b"p"))


def section_bytearray() -> None:
    # bytearray local after a mutation, and an empty one.
    ba = bytearray(b"z")
    ba.append(3)
    print("bytearray:", repr(ba))  # tpyc: ok
    print("bytearray:", repr(bytearray()))  # tpyc: ok
    # Quote choice follows bytes, but `'` is always escaped.
    print("bytearray:", repr(bytearray(b"it's")))  # tpyc: ok
    print("bytearray:", repr(bytearray(b'say "hi"')))  # tpyc: ok
    print("bytearray:", repr(bytearray(b"it's \"both\"")))  # tpyc: ok


def section_bytesview() -> None:
    b = b"ab'cd"
    # A bytes slice is a BytesView; it reprs like bytes.
    v = b[1:4]  # tpyc: type(BytesView)
    print("bytesview:", repr(v))  # tpyc: ok
    print("bytesview:", v, f"{v!r}")  # tpyc: ok


def section_fstring() -> None:
    # `!r` and the plain placeholder render the same text as repr().
    b = b"q\x00"
    ba = bytearray(b"w")
    r = range(1, 4)
    print(f"fstring: {b!r} {ba!r} {r!r}")  # tpyc: ok
    print(f"fstring: {b} {ba} {r}")  # tpyc: ok


def section_container() -> None:
    # Annotated because repr() rejects a pending list (BUGS.md#repr-pending-literal-arg).
    xs: list[bytes] = [b"a", b"\n"]
    # A container of bytes reprs each element as bytes.
    print("container:", repr(xs))  # tpyc: ok
    print("container:", xs)


def section_range() -> None:
    # Start kept when 0, step shown unless 1, at every element width.
    print("range:", repr(range(3)))  # tpyc: ok
    print("range:", repr(range(2, 5)))  # tpyc: ok
    print("range:", repr(range(0, 10, 2)))  # tpyc: ok
    print("range:", repr(range(10, 0, -3)))  # tpyc: ok
    print("range:", repr(range(int64(5), int64(-1), int64(-1))))  # tpyc: ok
    n: int = 10 ** 20
    print("range:", repr(range(n, n + 2)))  # tpyc: ok


def section_str_range() -> None:
    # str() of a range is its repr.
    print("str_range:", str(range(3)))  # tpyc: ok
    print("str_range:", str(range(1, 9, 4)))  # tpyc: ok


def section_small_range() -> None:
    # int8 / uint8 ranges print their bounds as numbers, not characters.
    print("small_range:", repr(range(int8(1), int8(3))))  # tpyc: ok
    print("small_range:", str(range(int8(-2), int8(2))))  # tpyc: ok
    print("small_range:", repr(range(uint8(0), uint8(9), uint8(3))))  # tpyc: ok
    print("small_range:", str(range(uint8(200), uint8(250), uint8(25))))  # tpyc: ok


class Holder:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def show(self, n: int32) -> str:
        r = range(n)
        # repr() and `!r` of a bytes field and a range inside a method.
        return repr(self.data) + f" {self.data!r} {r}"  # tpyc: ok


def section_method() -> None:
    h = Holder(b"m'")
    print("method:", h.show(2))


def reprs(b: bytes, n: int32) -> Iterator[str]:
    # repr() / `!r` of bytes and range across a yield.
    yield repr(b)  # tpyc: ok
    yield f"{b!r} {range(n, 0, -1)!r}"  # tpyc: ok


def section_generator() -> None:
    for s in reprs(b"g\x01", 3):
        print("generator:", s)


def section_inverse() -> None:
    # print() and str() give the same text as repr() for bytes, bytearray and range.
    b = b"x'y"
    print("inverse:", b)
    print("inverse:", str(b))
    print("inverse:", str(bytearray(b"k")))
    print("inverse:", range(4))


def main() -> None:
    section_bytes()
    section_bytearray()
    section_bytesview()
    section_fstring()
    section_container()
    section_range()
    section_str_range()
    section_small_range()
    section_method()
    section_generator()
    section_inverse()


main()
