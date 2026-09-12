# A resumable generator frame captures a bytearray / Array param and yields it:
# both the frame-param family and the yield slot read the reference axis, so
# bytearray and Array ride the same reference frame field list/dict/set do.
from typing import Iterator
from tpy import int32, Array


def twice_buf(b: bytearray) -> Iterator[bytearray]:  # tpyc: ok
    yield b  # tpyc: ok
    yield b


def twice_arr(a: Array[int32, 2]) -> Iterator[Array[int32, 2]]:  # tpyc: ok
    yield a  # tpyc: ok
    yield a


def twice_list(xs: list[int32]) -> Iterator[list[int32]]:  # tpyc: ok
    yield xs
    yield xs


def main() -> None:
    # Each leg mutates through the yielded borrow and reads the caller's
    # object afterwards -- a frame COPY would leave the originals untouched.
    b = bytearray(b"a")
    for got in twice_buf(b):
        got.append(66)
    print(len(b))

    a = Array[int32, 2]()
    for ga in twice_arr(a):
        ga[0] = ga[0] + 1
    print(a[0])

    xs = [1]
    for gx in twice_list(xs):
        gx.append(2)
    print(len(xs))


main()
