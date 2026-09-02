from tpy import Int32
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def f(boxes: list[Box]) -> Int32:
    for i in range(len(boxes)):
        r = boxes[i]
    return r.n
def main() -> None:
    print(f([Box(1), Box(2)]))
main()
