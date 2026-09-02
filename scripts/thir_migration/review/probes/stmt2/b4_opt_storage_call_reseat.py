from tpy import Int32
class Node:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
from tpy import Own
def mk(n: Int32) -> Own[Node | None]:
    if n > 0:
        return Node(n)
    return None
def f(items: list[Node], n: Int32) -> Int32:
    v = mk(n)
    v = items[0]
    if v is not None:
        return v.x
    return 0
def main() -> None:
    print(f([Node(1)], 3))
main()
