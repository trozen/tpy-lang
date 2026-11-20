# A `result=` map names every parameter that can lend storage: `label` (a
# `str` view) is left out, so what the result holds of it is undeclared.
from tpy.extern import native


@native
class Node:
    v: int


@native(result={"a": "borrow"})
def pick(a: Node, label: str) -> Node: ...  # tpyc: error(/result= does not say what the result holds of 'label' \(str\); add "label": "none" if nothing/)


def main() -> None:
    a = Node()
    print(pick(a, "x").v)


main()
