# 8a.4: const T& / const Base& for non-mutated protocol-typed params.
# A protocol param is non-mutated when all called methods are @readonly
# (or the param isn't used at all). The generated C++ uses const T_x& / const Base&.
from tpy import int32, dynamic, readonly
from typing import Protocol


class Measurable(Protocol):
    @readonly
    def measure(self) -> int32: ...


class Resizable(Protocol):
    @readonly
    def measure(self) -> int32: ...
    def resize(self, v: int32) -> None: ...


# Non-mutated static protocol param -> const T_p&
def get_measure(p: Measurable) -> int32:
    return p.measure()


# Mutated static protocol param -> T_p& (resize modifies p)
def double_resize(p: Resizable) -> int32:
    p.resize(p.measure() * 2)
    return p.measure()


# Two params: src is non-mutated (const T&), dst is mutated (T&)
def copy_measure(src: Resizable, dst: Resizable) -> None:
    dst.resize(src.measure())


@dynamic
class Shape(Protocol):
    @readonly
    def area(self) -> int32: ...


class Rect:
    _w: int32
    _h: int32

    def __init__(self, w: int32, h: int32) -> None:
        self._w = w
        self._h = h

    @readonly
    def area(self) -> int32:
        return self._w * self._h


# @dynamic protocol: non-mutated param -> const Base&
def print_area(s: Shape) -> None:
    print(s.area())


class Box:
    _side: int32

    def __init__(self, side: int32) -> None:
        self._side = side

    @readonly
    def measure(self) -> int32:
        return self._side

    def resize(self, v: int32) -> None:
        self._side = v


def main() -> None:
    b = Box(10)
    print(get_measure(b))
    print(double_resize(b))
    print(get_measure(b))
    copy_measure(Box(5), b)
    print(b.measure())
    print_area(Rect(3, 4))


main()
