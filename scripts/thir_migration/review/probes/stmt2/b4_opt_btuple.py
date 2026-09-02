from tpy import Int32
class Node:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
def first_two(items: list[Node]) -> tuple[Node, Node]:
    return (items[0], items[1])
def f(items: list[Node], flag: bool) -> Int32:
    t: tuple[Node, Node] | None = None
    if flag:
        t = first_two(items)
    if t is not None:
        return t[0].x
    return 0
def main() -> None:
    print(f([Node(1), Node(2)], True))
main()
