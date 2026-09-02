from tpy import Int32
class Node:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
def f(a: Node, b: Node, flag: bool) -> Int32:
    x: Node | None = None
    if flag:
        x = b
    if x is not None:
        return x.x
    return 0
def main() -> None:
    print(f(Node(1), Node(2), True))
main()
