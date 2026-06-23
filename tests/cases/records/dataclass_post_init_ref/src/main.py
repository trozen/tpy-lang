# __post_init__ can populate a reference-type (list) field. The list it builds is
# stored on self and then mutated after construction to prove it is aliased, not
# copied -- a read-only check would be parity-blind to a silent copy.
from dataclasses import dataclass, field

@dataclass
class Bag:
    n: int
    items: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.items = [self.n, self.n + 1]

def main() -> None:
    b = Bag(5)
    b.items.append(99)
    print(b.items)

main()
