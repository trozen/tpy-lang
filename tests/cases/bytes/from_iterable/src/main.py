# bytes()/bytearray() constructors and bytearray.extend accept iterables of
# Int32 (runtime range-checked) or UInt8 (fast path), plus generator exprs.
from tpy import Int32, UInt8


def main() -> None:
    # Iterable[Int32] via list
    xs: list[Int32] = [10, 20, 30]
    print(bytes(xs))

    # Iterable[UInt8] via list
    ys: list[UInt8] = [1, 2, 3]
    print(bytes(ys))

    # Generator expression of Int32 (the original user case from the REPL)
    print(bytes(x * 2 for x in xs))

    # bytearray from iterable
    print(bytearray(xs))
    print(bytearray(x + 1 for x in xs))

    # bytearray.extend with Int32 iterable
    ba = bytearray()
    ba.extend(xs)
    print(ba)

    # bytearray.extend with UInt8 iterable
    ba.extend(ys)
    print(ba)

    # bytearray.extend with another bytearray (exercises range-path specialization)
    other = bytearray([50, 60])
    ba.extend(other)
    print(ba)

    # bytearray.extend with a generator of UInt8 (exercises the __next__ fallback
    # in bytes_extend_byte_iterable since generators aren't input_range).
    ba.extend(v for v in ys)
    print(ba)

    # Boundary values 0 and 255 round-trip intact.
    print(bytes([0, 255]))

    # bytes from bytearray uses the bytes_copy fast path (verified via snapshot).
    print(bytes(ba))


main()
