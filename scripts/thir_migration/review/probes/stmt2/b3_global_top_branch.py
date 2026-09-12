from tpy import int32
class Node:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
g: Node | None = None
flag = True
if flag:
    g = Node(3)
def main() -> None:
    print(0)
main()
