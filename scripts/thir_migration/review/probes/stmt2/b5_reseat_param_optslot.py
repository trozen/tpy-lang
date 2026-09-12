from tpy import int32
class Node:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
def f(a: Node, b: Node, flag: bool) -> int32:
    x: Node | None = None
    if flag:
        x = b
    if x is not None:
        return x.x
    return 0
def main() -> None:
    print(f(Node(1), Node(2), True))
main()
