from tpy import int32
from tplib import Box
class Node:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v
    def pair(self) -> tuple[int32, int32]:
        return (self.v, self.v)
    def maybe(self) -> int32 | None:
        return self.v
def main() -> None:
    b = Box(Node(2))
    t = b.pair()
    m = b.maybe()
    if m is not None:
        print(t[0] + m)
main()
