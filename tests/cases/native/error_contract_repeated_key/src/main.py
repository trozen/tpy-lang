# A key written twice in a `result=` map is refused: the dict literal would
# keep only its last value and silently drop the first declaration.
from tpy.extern import native


@native
class Node:
    v: int


@native(result={"a": "borrow", "a": "none"})  # tpyc: error(/result= names 'a' twice/)
def pick(a: Node) -> Node: ...


def main() -> None:
    a = Node()
    print(pick(a).v)


main()
