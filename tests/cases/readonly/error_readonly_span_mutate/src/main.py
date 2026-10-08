# Readonly does not reach through a Span field; a Span[readonly[T]] field
# protects its elements, through a mutable receiver as well.
from tpy import int32, Span, Array, readonly

class Box:
    items: Span[readonly[int32]]
    def __init__(self, items: Span[readonly[int32]]) -> None:
        self.items = items

def mutate_box(b: Box) -> None:
    b.items[0] = 99  # tpyc: error(/Cannot assign.*read-only/)

def main() -> None:
    pass

main()
