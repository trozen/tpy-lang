# __copy__ on a plain copyable class overrides default copy behavior
from __future__ import annotations
from tpy import Int32, Own, copy


class Counter:
    count: Int32

    def __init__(self, count: Int32):
        self.count = count

    def __copy__(self) -> Own[Counter]:
        return Counter(self.count * 2)


def main():
    a = Counter(5)
    b = copy(a)
    print(a.count)
    print(b.count)


main()
