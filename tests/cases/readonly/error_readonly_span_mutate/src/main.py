# Mutation through @readonly ref's Span field is rejected (becomes Span[readonly[T]]).
from tpy import int32, Span, Array, readonly

class Box:
    items: Span[int32]
    def __init__(self, items: Span[int32]) -> None:
        self.items = items

def mutate_box(b: readonly[Box]) -> None:
    b.items[0] = 99  # tpyc: error(/Cannot assign.*read-only/)

def main() -> None:
    pass

main()
