# Test implicit coercion from type with __span__() to Span parameters.
# Covers both mutable and readonly __span__ returns.
from tpy import Int32, Span, readonly, auto_readonly

class ROBuffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [1, 2, 3]

    def __span__(self) -> Span[readonly[Int32]]:
        return self._data

class MutBuffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [10, 20, 30]

    @auto_readonly
    def __span__(self) -> Span[auto_readonly[Int32]]:
        return self._data

def sum_ro(items: Span[readonly[Int32]]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    ro = ROBuffer()
    print(sum_ro(ro))
    mut = MutBuffer()
    print(sum_ro(mut))

main()
