# T: Send generic bounds on functions and records, satisfied at call /
# construction sites (Int32 and list[Int32] are Send).
from tpy import Int32, Send

class Channel[T: Send]:
    item: T

    def __init__(self, item: T) -> None:
        self.item = item

def use[T: Send](x: T) -> Int32:
    return 1

def main() -> None:
    print(use(42), use([1, 2]))
    ch = Channel(7)
    print(ch.item)

main()
