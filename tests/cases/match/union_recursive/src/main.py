# match/case on a recursive union type alias
class Leaf:
    value: int

    def __init__(self, value: int) -> None:
        self.value = value


type Tree = Leaf | list[Tree]


def describe(t: Tree) -> str:
    match t:
        case Leaf(value=v):
            return "leaf=" + str(v)
        case _:
            return "branch"


def main() -> None:
    a: Tree = Leaf(42)
    b: list[Tree] = [Leaf(1), Leaf(2)]
    print(describe(a))
    print(describe(b))

main()
