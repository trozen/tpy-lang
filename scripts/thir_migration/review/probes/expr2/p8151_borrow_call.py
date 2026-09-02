from tpy import Int32
class Node:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
def borrow(xs: list[Node]) -> list[Node]:
    return xs
def main() -> None:
    b: list[Node] = [Node(1)]
    a = list(borrow(b))
    print(len(a), len(b))
main()
