# Iterator with only __next__, verify __iter__ auto-synthesis works
from tpy import Int32

class SimpleIter:
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

def main() -> None:
    # Auto-synthesized __iter__ enables for-loop
    for x in SimpleIter(4):
        print(x)
    print("done")

main()
