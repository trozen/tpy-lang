from tpy import int32
class Node:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
    def __len__(self) -> int32:
        return self.n
class H:
    node: Node | None
    def __init__(self) -> None:
        self.node = Node(2)
    def get(self) -> Node | None:
        return self.node
def main() -> None:
    h = H()
    if h.get():
        print("x")
main()
