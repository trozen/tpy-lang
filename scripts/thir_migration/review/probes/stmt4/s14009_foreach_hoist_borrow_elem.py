from tpy import int32
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def f(boxes: list[Box]) -> int32:
    for i in range(len(boxes)):
        r = boxes[i]
    return r.n
def main() -> None:
    print(f([Box(1), Box(2)]))
main()
