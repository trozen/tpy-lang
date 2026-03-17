# Tests calling __next__() via try/except on a type that only defines __next_opt__
from tpy import Int32

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

def main() -> None:
    c = Counter(3)
    while True:
        try:
            v = c.__next__()
        except StopIteration:
            break
        print(v)

    # Verify exhaustion
    exhausted = False
    try:
        c.__next__()
    except StopIteration:
        exhausted = True
    if exhausted:
        print("exhausted")

main()
