# Calling __next__() without try/except is a compile error
from tpy import int32

class Counter:
    current: int32
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.current = 0
        self.limit = limit

    def __next__(self) -> int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration

c = Counter(5)
c.__next__()  # tpyc: error(/must be handled with try/except/)
