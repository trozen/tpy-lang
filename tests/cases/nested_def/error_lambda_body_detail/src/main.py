# A multi-line callback rejection points at the lambda and retains its body cause.
class Node:
    name: str

    def __init__(self, name: str):
        self.name = name


def main():
    nodes = [Node("pear"), Node("apple")]
    # The body's lowering gap (a tuple literal's element read,
    # BUGS.md#tuple-literal-subscript-rejects) must survive the enclosing
    # sorted call.
    result = sorted(
        nodes,
        key=lambda n: (n.name, 1)[0],  # tpyc: error(/expr\.lambda:lambda\.body:subscript\.tuple_shape/)
    )


main()
