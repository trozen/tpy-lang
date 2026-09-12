from tpy import int32
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def pair(b: Box) -> tuple[Box, int32]:
    return (b, 1)
def f(boxes: list[Box]) -> int32:
    r = boxes[0]
    r, n = pair(boxes[1])
    return r.n + n
def main() -> None:
    print(f([Box(1), Box(2)]))
main()
