# TPy ACCEPTS an unbound-self field read (`Base.items`) inside a subclass
# method and resolves it to THIS object's storage -- a divergence, since
# CPython raises AttributeError for it (BUGS.md#unbound-self-field-read-binds-self).
# What this case pins is the borrow tracker keying that read at `self.items`:
# a loan taken out of the one spelling meets a mutation spelled the other way,
# and the iteration is warned about. Keyed at `Base.items` -- the receiver's
# syntactic class name -- no mutation through `self` could ever have met it.
# Lowering the `BaseN.field` container read itself is a separate, located
# gap, which is what stops this case.
from tpy import int32


class Base:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]


class Derived(Base):
    def __init__(self) -> None:
        Base.__init__(self)

    def grow(self) -> None:
        # the iteration borrows Base.items; the append mutates the same
        # storage under its `self` spelling
        for x in Base.items:  # tpyc: error(/not yet supported by C\+\+ code generation/)
            self.items.append(x)  # tpyc: warning(/Mutation of 'self.items' while iterating over it.*'append' invalidates the iterator/)


def main() -> None:
    Derived().grow()


main()
