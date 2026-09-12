from tpy import int32
class Node:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
def pick(flag: bool, k: int32) -> int32:
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
