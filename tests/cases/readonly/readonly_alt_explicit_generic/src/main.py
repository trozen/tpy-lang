# Explicit readonly_alt[T] annotation on generic class method.
# The const overload becomes span<const T> while mutable stays span<T>.
from tpy import Int32, Span, ReadOnlySpan, readonly, readonly_alt

class Vec[T]:
    _data: list[T]

    def __init__(self) -> None:
        self._data = []

    def push(self, v: T) -> None:
        self._data.append(v)

    @readonly_alt
    def data(self) -> Span[readonly_alt[T]]:
        return self._data


def read_vec(v: readonly[Vec[Int32]]) -> None:
    s = v.data()  # tpyc: type(ReadOnlySpan[Int32])
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
