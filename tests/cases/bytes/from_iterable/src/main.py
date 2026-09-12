# bytes()/bytearray() constructors and bytearray.extend accept iterables of
# int32 (runtime range-checked) or uint8 (fast path), plus generator exprs.
from tpy import int32, uint8


def main() -> None:
    # Iterable[int32] via list
    xs: list[int32] = [10, 20, 30]
    print(bytes(xs))

    # Iterable[uint8] via list
    ys: list[uint8] = [1, 2, 3]
    print(bytes(ys))

    # Generator expression of int32 (the original user case from the REPL)
    print(bytes(x * 2 for x in xs))

    # bytearray from iterable
    print(bytearray(xs))
    print(bytearray(x + 1 for x in xs))

    # bytearray.extend with int32 iterable
    ba = bytearray()
    ba.extend(xs)
    print(ba)

    # bytearray.extend with uint8 iterable
    ba.extend(ys)
    print(ba)

    # bytearray.extend with another bytearray (exercises range-path specialization)
    other = bytearray([50, 60])
    ba.extend(other)
    print(ba)

    # bytearray.extend with a generator of uint8 (exercises the __next__ fallback
    # in tpy::extend since generators aren't std::ranges::input_range).
    ba.extend(v for v in ys)
    print(ba)

    # Boundary values 0 and 255 round-trip intact.
    print(bytes([0, 255]))

    # bytes from bytearray uses the bytes_copy fast path (verified via snapshot).
    print(bytes(ba))


main()
