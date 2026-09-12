from tpy import int32, Span, Array

class Box:
    items: Array[int32, 3]

    def __init__(self, items: Array[int32, 3]) -> None:
        self.items = items

def main() -> None:
    arr: Array[int32, 3] = [1, 2, 3]
    box: Box = Box(arr)
    s: Span[int32] = box.items
    print(s[0])
    print(s[2])

main()
