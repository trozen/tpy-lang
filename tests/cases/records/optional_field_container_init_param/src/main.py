# Optional[container] parameter in explicit __init__: the codegen must use
# the param's inner type (list) for the temp slot, not the sema-inferred
# expression type (array) from the fixed-size list literal.
from tpy import Int32
from typing import Optional

class Holder:
    items: list[Int32]

    def __init__(self, items: Optional[list[Int32]]) -> None:
        if items is not None:
            self.items = items
        else:
            self.items = []

def main() -> None:
    h1 = Holder([10, 20])
    h2 = Holder(None)
    print(len(h1.items))
    print(len(h2.items))

main()
