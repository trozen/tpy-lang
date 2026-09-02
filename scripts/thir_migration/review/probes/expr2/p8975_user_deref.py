from tpy import Int32
from tplib import Box
class Node:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
    def pair(self) -> tuple[Int32, Int32]:
        return (self.v, self.v)
    def maybe(self) -> Int32 | None:
        return self.v
def main() -> None:
    b = Box(Node(2))
    t = b.pair()
    m = b.maybe()
    if m is not None:
        print(t[0] + m)
main()
