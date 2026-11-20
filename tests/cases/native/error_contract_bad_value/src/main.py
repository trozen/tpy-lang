# A `result=` relation outside the closed set ("borrow", "element", "adopts",
# "none") is a declaration error naming the key and the value.
from tpy.extern import native


@native
class Node:
    v: int


@native(result={"a": "borrows", "b": "none"})  # tpyc: error(/gives 'a' the relation 'borrows'; a result relation is "borrow", "element", "adopts" or "none"/)
def pick(a: Node, b: Node) -> Node: ...


def main() -> None:
    a = Node()
    print(pick(a, a).v)


main()
