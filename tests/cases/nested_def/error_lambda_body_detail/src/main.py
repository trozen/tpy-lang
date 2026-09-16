# A multi-line callback rejection points at the lambda and retains its body cause.
class Node:
    name: str

    def __init__(self, name: str):
        self.name = name


def main():
    nodes = [Node("pear"), Node("apple")]
    # The field-result lowering gap must survive the enclosing sorted call.
    result = sorted(
        nodes,
        key=lambda n: n.name,  # tpyc: error(/expr\.lambda:lambda\.body:field\.result_type/)
    )


main()
