from tpy import Int32, Own
class Node:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
def mk(n: Int32) -> Own[Node]:
    return Node(n)
def f(n: Int32, flag: bool) -> Int32:
    v = Node(n)
    v = mk(n) if flag else mk(n + 1)
    return v.x
def main() -> None:
    print(f(3, True))
main()
