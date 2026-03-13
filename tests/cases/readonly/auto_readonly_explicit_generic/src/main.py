# Explicit auto_readonly[T] annotation on generic class method.
# The const overload becomes span<const T> while mutable stays span<T>.
from tpy import Int32, Span, readonly, auto_readonly

class Vec[T]:
    _data: list[T]

    def __init__(self) -> None:
        self._data = []

    def push(self, v: T) -> None:
        self._data.append(v)

    @auto_readonly
    def data(self) -> Span[auto_readonly[T]]:
        return self._data


def read_vec(v: readonly[Vec[Int32]]) -> None:
    s = v.data()  # tpyc: type(Span[readonly[Int32]])
    print(s[Int32(0)])
    print(s[Int32(1)])


def main() -> None:
    v = Vec[Int32]()
    v.push(Int32(10))
    v.push(Int32(20))
    s = v.data()  # tpyc: type(Span[Int32])
    print(s[Int32(0)])
    read_vec(v)


main()
