from tpy import Int32, Own

class RangeIter:
    current: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.current = start
        self.limit = limit

    def __next__(self) -> Int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration

class NumberRange:
    start: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[RangeIter]:
        return RangeIter(self.start, self.limit)

# 1. Container with __iter__ in for-loop
for x in NumberRange(0, 5):
    print(x)

# 2. Can iterate again (fresh iterator each time)
nums = NumberRange(10, 13)
for x in nums:
    print(x)
for x in nums:
    print(x)

print("done")
