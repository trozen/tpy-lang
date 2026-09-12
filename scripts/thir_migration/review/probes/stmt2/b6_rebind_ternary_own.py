from tpy import int32, Own
class Node:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
def mk(n: int32) -> Own[Node]:
    return Node(n)
def f(n: int32, flag: bool) -> int32:
    v = Node(n)
    v = mk(n) if flag else mk(n + 1)
    return v.x
def main() -> None:
    print(f(3, True))
main()
