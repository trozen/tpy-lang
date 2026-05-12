# Regression: a user-defined class named `Box` that does NOT actually own
# a Ptr-typed field must NOT be treated as cycle-breaking. The old hardcoded
# name list ("Box", "list", ...) silently accepted this as indirecting; the
# structural-field-walk rule correctly rejects it.

class Lit:
    value: int

    def __init__(self, value: int) -> None:
        self.value = value


class Box[T]:
    # No Ptr / list / dict / set / Optional field -- so this "Box" does not
    # provide indirection, despite its name. Cycle detection must report an
    # infinite-size error.
    value: T

    def __init__(self, value: T) -> None:
        self.value = value


type Expr = Lit | Box[Expr]  # tpyc: error(/infinite-size|direct recursion/)
