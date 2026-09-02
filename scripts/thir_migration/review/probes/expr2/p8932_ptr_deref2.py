from tpy import Int32, Ptr
class Node:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
    def pair(self) -> tuple[Int32, Int32]:
        return (self.v, self.v)
    def maybe(self) -> Int32 | None:
        return self.v
    def get(self) -> Int32:
        return self.v
def f(p: Ptr[Node]) -> Int32:
    t = p.pair()
    return t[0]
def g(p: Ptr[Node]) -> Int32:
    m = p.maybe()
    if m is not None:
        return m
    return 0
def h(p: Ptr[Node]) -> Int32:
    return p.get()
def main() -> None:
    n = Node(2)
    p = Ptr[Node]()
    print(h(p))
main()
