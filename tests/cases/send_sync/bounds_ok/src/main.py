# T: Send generic bounds on functions and records, satisfied at call /
# construction sites (int32 and list[int32] are Send).
from tpy import int32, Send

class Channel[T: Send]:
    item: T

    def __init__(self, item: T) -> None:
        self.item = item

def use[T: Send](x: T) -> int32:
    return 1

def main() -> None:
    print(use(42), use([1, 2]))
    ch = Channel(7)
    print(ch.item)

main()
