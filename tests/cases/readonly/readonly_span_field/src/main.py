# @readonly propagation turns Span field into Span[readonly[T]] for reading.
from tpy import int32, Span, Array, readonly

class Box:
    items: Span[int32]
    def __init__(self, items: Span[int32]) -> None:
        self.items = items

def read_box(b: readonly[Box]) -> int32:
    return b.items[0]

def main() -> None:
    arr: Array[int32, 3] = [10, 20, 30]
    b = Box(arr)
    print(read_box(b))  # 10

main()
