from tpy import Int32
class Node:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
g: Node | None = None
flag = True
if flag:
    g = Node(3)
def main() -> None:
    print(0)
main()
