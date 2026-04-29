# Instance-side write to a mutable ClassVar: `obj.X = ...` writes through to
# the class-scoped storage, not an instance attribute (as PEP 526 prescribes).
# TPy warns on the divergence -- mypy/pyright already flag this pattern, and
# the user can write `Counter.instances = ...` to silence it.
from typing import ClassVar
from tpy import Int32


class Counter:
    instances: ClassVar[Int32] = 0

    def __init__(self) -> None:
        pass


def main() -> None:
    a = Counter()
    a.instances = 5  # tpyc: warning(/Assigning to ClassVar 'Counter.instances' via instance/)
    print(Counter.instances)
    print(a.instances)
    b = Counter()
    b.instances = 7  # tpyc: warning(/Assigning to ClassVar 'Counter.instances' via instance/)
    print(Counter.instances)
    print(a.instances)


main()
