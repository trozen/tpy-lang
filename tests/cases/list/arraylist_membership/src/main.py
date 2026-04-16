# Test `in` operator, comprehension, and for-loop on user NativeIterable.
# Exercises the __iter__() capture path in codegen for types with
# synthesized begin()/end().
from tpy import Int32, Array, Span, SpanIter, readonly, auto_readonly
from tplib import ArrayList


class SimpleBuffer:
    """User NativeIterable WITHOUT __contains__ -- exercises the IIFE
    capture path in the `in` operator fallback (std::find via __iter__)."""
    _data: Array[Int32, 4]
    _n: Int32

    def __init__(self) -> None:
        self._data = [10, 20, 30, 0]
        self._n = 3

    @auto_readonly
    def __span__(self) -> Span[auto_readonly[Int32]]:
        return self._data

    @auto_readonly
    def __iter__(self) -> SpanIter[auto_readonly[Int32]]:
        return SpanIter(self.__span__())


def main() -> None:
    a = ArrayList[Int32, 8]()
    a.append(10)
    a.append(20)
    a.append(30)

    # `in` on ArrayList (has __contains__ -- direct call path)
    print(20 in a)
    print(99 in a)

    # `in` on SimpleBuffer (no __contains__ -- IIFE std::find path)
    b = SimpleBuffer()
    print(20 in b)
    print(99 in b)

    # list comprehension over user NativeIterable
    doubled = [x * 2 for x in a]
    print(doubled)

    # set comprehension over user NativeIterable
    unique = {x // 10 for x in a}
    print(len(unique))

    # for-loop (universal default path)
    total: Int32 = 0
    for x in a:
        total += x
    print(total)


main()
