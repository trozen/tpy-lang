# Iterator with only __next__, verify __iter__ auto-synthesis works
from tpy import int32

class SimpleIter:
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

def main() -> None:
    # Auto-synthesized __iter__ enables for-loop
    for x in SimpleIter(4):
        print(x)
    print("done")

main()
