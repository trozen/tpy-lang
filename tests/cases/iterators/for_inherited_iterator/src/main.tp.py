from tpy import Int32, OptIterator

class Counter:
    current: Int32
    limit: Int32

    def __init__(self, limit: Int32) -> None:
        self.current = 0
        self.limit = limit

    def __next_opt__(self) -> Int32 | None:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        return None

class DoubleCounter(Counter):
    def __init__(self, limit: Int32) -> None:
        super().__init__(limit * 2)

# Multi-level: GrandChild -> DoubleCounter -> Counter
class GrandChild(DoubleCounter):
    def __init__(self, limit: Int32) -> None:
        super().__init__(limit)

# 1. for-loop over child inheriting next() from parent
for x in DoubleCounter(3):
    print(x)

# 2. Pass inherited iterator to OptIterator[Int32] param
def sum_iter(it: OptIterator[Int32]) -> Int32:
    total: Int32 = 0
    for x in it:
        total += x
    return total

print(sum_iter(DoubleCounter(3)))

# 3. Multi-level: for-loop + protocol param
for x in GrandChild(2):
    print(x)
print(sum_iter(GrandChild(2)))
