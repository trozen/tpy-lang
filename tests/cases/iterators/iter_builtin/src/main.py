# Tests iter() builtin and explicit __next__() with try/except
from tpy import Int32, Own

class CounterIter:
    current: Int32
    limit: Int32

    def __init__(self, limit: Int32) -> None:
        self.current = 0
        self.limit = limit

    def __next__(self) -> Int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration

class Counter:
    limit: Int32

    def __init__(self, limit: Int32) -> None:
        self.limit = limit

    def __iter__(self) -> Own[CounterIter]:
        return CounterIter(self.limit)

def main() -> None:
    # iter() returns an iterator from an iterable
    c = Counter(4)
    it = iter(c)

    # Advance iterator with try/except
    while True:
        try:
            v = it.__next__()
        except StopIteration:
            break
        print(v)

    # Exhausted iterator raises StopIteration
    exhausted = False
    try:
        it.__next__()
    except StopIteration:
        exhausted = True
    if exhausted:
        print("exhausted")

main()
