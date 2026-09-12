from tpy import int32, Ptr
class Node:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v
    def pair(self) -> tuple[int32, int32]:
        return (self.v, self.v)
    def maybe(self) -> int32 | None:
        return self.v
    def get(self) -> int32:
        return self.v
def f(p: Ptr[Node]) -> int32:
    t = p.pair()
    return t[0]
def g(p: Ptr[Node]) -> int32:
    m = p.maybe()
    if m is not None:
        return m
    return 0
def h(p: Ptr[Node]) -> int32:
    return p.get()
def main() -> None:
    n = Node(2)
    p = Ptr[Node]()
    print(h(p))
main()
