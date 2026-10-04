# min() / max() with key= over tuples that hold a class instance are refused:
# the tuple is a value type, but the value overload would copy the instance
# it holds, and the reference overload takes no tuple -- so neither takes it
# until the result can be the tuple's borrow form (docs/LANGUAGE_FEATURES.md,
# the builtins section; BUGS.md#min-max-key-result-copies).
class Node:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def main() -> None:
    ranked: list[tuple[Node, int]] = [(Node(1), 5), (Node(2), 3)]
    node, rank = min(ranked, key=lambda t: t[1])  # tpyc: error(/Type 'tuple\[Node, int\]' does not satisfy 'ValueType' required by 'min'/)
    node.v = 100
    print(ranked[1][0].v, rank)


main()
