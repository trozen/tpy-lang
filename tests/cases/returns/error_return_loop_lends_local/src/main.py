# A loop variable over a call that may lend a local is no durable root:
# returning it by reference dangles (LANGUAGE_FEATURES "Owned Return Values").
class Node:
    def __init__(self, v: int) -> None:
        self.v = v


def pick(a: list[Node], b: list[Node]) -> list[Node]:
    return a if len(a) > len(b) else b


def first_big(ps: list[Node]) -> Node:
    local = [Node(100), Node(200)]
    for x in pick(ps, local):
        if x.v > 50:
            return x  # tpyc: error(/Cannot return local or temporary as reference/)
    return ps[0]


def main() -> None:
    print(first_big([Node(1)]).v)


main()
