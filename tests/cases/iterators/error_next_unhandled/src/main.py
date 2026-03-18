# next() called outside try/except should error
from tpy import Int32, error_return

class Counter:
    value: Int32
    limit: Int32
    def __init__(self, limit: Int32) -> None:
        self.value = 0
        self.limit = limit
    def __iter__(self) -> Counter:
        return self
    @error_return(StopIteration)
    def __next__(self) -> Int32:
        if self.value >= self.limit:
            raise StopIteration
        v = self.value
        self.value += 1
        return v

def main() -> None:
    c = Counter(3)
    it = iter(c)
    v = next(it)  # tpyc: error(/must be handled with try/)
    print(v)

main()
