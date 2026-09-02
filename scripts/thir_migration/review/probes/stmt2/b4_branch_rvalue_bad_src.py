from tpy import Int32
class Node:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
def f(items: list[Node], flag: bool, n: Int32) -> Int32:
    v: Node | None = None
    if flag:
        v = Node(n)
    else:
        v = items[0]
    if v is not None:
        return v.x
    return 0
def main() -> None:
    print(f([Node(1)], True, 3))
main()
