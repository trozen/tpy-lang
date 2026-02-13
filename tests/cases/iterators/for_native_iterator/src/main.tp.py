from tpy import Int32, OptIterator

class Counter:
    current: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.current = start
        self.limit = limit

    def __next_opt__(self) -> Int32 | None:
        if self.current < self.limit:
            val = self.current
            self.current += 1
            return val
        return None

def sum_iter(it: OptIterator[Int32]) -> Int32:
    total: Int32 = 0
    for x in it:
        total += x
    return total

def count_iter(it: OptIterator[Int32]) -> Int32:
    n: Int32 = 0
    for x in it:
        n += 1
    return n

def first_or_fallback(it: OptIterator[Int32], fallback: Int32) -> Int32:
    for x in it:
        return x
    return fallback

# Pass Counter objects (which extend OptIterator[Int32])
print(sum_iter(Counter(0, 5)))          # 0+1+2+3+4 = 10
print(sum_iter(Counter(1, 6)))          # 1+2+3+4+5 = 15

print(count_iter(Counter(0, 7)))        # 7
print(count_iter(Counter(0, 0)))        # 0 (empty iterator)

print(first_or_fallback(Counter(0, 3), -1))   # 0
print(first_or_fallback(Counter(0, 0), -1))   # -1 (empty, returns fallback)
