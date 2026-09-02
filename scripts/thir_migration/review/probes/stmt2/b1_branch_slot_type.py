from tpy import Int32
class Node:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
def pick(flag: bool, k: Int32) -> Int32:
    if flag:
        sel: Node | None = None
        if k > 0:
            sel = Node(k)
        if sel is not None:
            return sel.x
        return -1
    return 0
def main() -> None:
    print(pick(True, 3))
main()
