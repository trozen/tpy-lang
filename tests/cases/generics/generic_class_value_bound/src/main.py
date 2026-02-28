# Generic class with T: ValueType bound -- no copy warnings expected
# Covers: field assignment, subscript assignment, free function bound
from tpy import Int32, Array, ValueType

class Box[T: ValueType]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value  # tpyc: ok

    def get(self) -> T:
        return self.value

    def set(self, value: T) -> None:
        self.value = value  # tpyc: ok


class Ring[T: ValueType]:
    data: Array[T, 4]
    size: Int32

    def __init__(self, fill: T) -> None:
        self.data = [fill, fill, fill, fill]
        self.size = 0

    def put(self, v: T) -> None:
        self.data[self.size] = v  # tpyc: ok
        self.size += 1

    def get(self, i: Int32) -> T:
        return self.data[i]


def identity[T: ValueType](v: T) -> T:
    return v


def main() -> None:
    box_int: Box[Int32] = Box[Int32](42)
    print(box_int.get())
    box_int.set(100)
    print(box_int.get())

    box_bool: Box[bool] = Box[bool](True)
    print(box_bool.get())
    box_bool.set(False)
    print(box_bool.get())

    ring: Ring[Int32] = Ring[Int32](0)
    ring.put(10)
    ring.put(20)
    print(ring.get(0))
    print(ring.get(1))

    v: Int32 = 99
    print(identity(v))


main()
