from tpy import Int32, NativeIterator

class Counter:
    current: Int32
    limit: Int32

    def __init__(self, limit: Int32) -> None:
        self.current = 0
        self.limit = limit

    def next(self) -> Int32 | None:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        return None

# 1. Direct use in for-loop (structural detection)
for x in Counter(5):
    print(x)

# 2. Pass to function taking NativeIterator[Int32] (structural conformance)
def sum_iter(it: NativeIterator[Int32]) -> Int32:
    total: Int32 = 0
    for x in it:
        total += x
    return total

print(sum_iter(Counter(5)))

# 3. Empty iterator
for x in Counter(0):
    print(x)
print("done")
