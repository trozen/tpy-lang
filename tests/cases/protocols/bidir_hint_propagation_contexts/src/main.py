# Inward LHS-hint propagation works in every context where sema reaches
# ``analyze_expr_with_hint(arg, target_type)`` -- not just variable-with-
# annotation. This test exercises the four most common alternate sources
# of expr_type_hint: function-arg position, return statement, field
# assignment via ctor arg, and list-literal element under an annotated
# container type. Each one runs a 3-level Rc[Box[Box[Pet]]] chain (or
# 2-level for the list element case) that would fail to infer if the seed
# didn't reach the inner constructor.
from typing import Protocol
from tpy import dynamic, Own
from tplib import Box, Rc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


# Context 1: function argument position. The outer call's per-arg hint
# propagation gives the inner Rc.new(...) call an Rc[Box[Box[Pet]]] hint.
def take(r: Own[Rc[Box[Box[Pet]]]]) -> str:
    return r.get().get().get().name()


# Context 2: return-statement context. The return-type hint reaches the
# inner Rc.new(...) call as if it were a LHS annotation.
def make() -> Own[Rc[Box[Box[Pet]]]]:
    return Rc.new(Box(Box(Dog("Returned"))))


# Context 3: field assignment via constructor arg -- the Holder ctor
# param is the propagation surface.
class Holder:
    r: Rc[Box[Box[Pet]]]
    def __init__(self, r: Own[Rc[Box[Box[Pet]]]]) -> None:
        self.r = r


# Context 4: list-literal element under an annotated container type.
def collect() -> Own[list[Rc[Box[Pet]]]]:
    return [Rc.new(Box(Dog("Listed1"))), Rc.new(Box(Dog("Listed2")))]


def main() -> None:
    # 1. function arg
    print(take(Rc.new(Box(Box(Dog("Arg"))))))

    # 2. return
    r2 = make()
    print(r2.get().get().get().name())

    # 3. field via ctor arg
    h = Holder(Rc.new(Box(Box(Dog("Field")))))
    print(h.r.get().get().get().name())

    # 4. list literal
    xs = collect()
    for x in xs:
        print(x.get().get().name())


main()
