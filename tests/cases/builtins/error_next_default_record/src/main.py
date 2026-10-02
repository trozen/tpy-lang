# next(it, default) over class instances is refused: the result is returned by
# value, a copy where Python returns the element (or the default) itself
# (docs/LANGUAGE_FEATURES.md, the builtins section;
# BUGS.md#min-max-key-result-copies).
class Node:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def main() -> None:
    nodes = [Node(1), Node(2)]
    it = iter(nodes)
    first = next(it, Node(0))  # tpyc: error(/Type 'Node' does not satisfy 'ValueType' required by 'next'/)
    first.v = 100
    print(nodes[0].v)


main()
