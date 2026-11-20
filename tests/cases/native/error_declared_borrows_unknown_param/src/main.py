# `result={...: "borrow"}` names what a native result borrows; a name that
# is not a parameter of the binding is an error, not silently dropped.
from tpy.extern import native


@native
class Node:
    v: int


@native(result={"nope": "borrow"})
def pick(a: Node, b: Node) -> Node: ...  # tpyc: error(/'nope', which is not a parameter of 'pick'/)


def main() -> None:
    a = Node()
    print(pick(a, a).v)


main()
