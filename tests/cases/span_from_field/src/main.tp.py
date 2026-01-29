from tpy import Int32, Span, Array

class Box:
    items: Array[Int32, 3]

    def __init__(self, items: Array[Int32, 3]) -> None:
        self.items = items

def main() -> None:
    arr: Array[Int32, 3] = [1, 2, 3]
    box: Box = Box(arr)
    s: Span[Int32] = box.items
    print(s[0])
    print(s[2])

main()
