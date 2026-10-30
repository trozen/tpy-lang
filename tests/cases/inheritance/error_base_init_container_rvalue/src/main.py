# A fresh container at a base parameter the base MUTATES: the list it is
# lent must live in a named temporary, and a base initializer has no
# statement to hold one, so it is rejected
# (BUGS.md#base-init-args-separate-lowering). Read-only, the same argument
# binds for the initializer (calls/argument_sink, base_init_fresh_const_ref).
# The sibling spellings do not compile either: an owning call's list,
# `super().__init__(mk(n))`, is refused the same way, and a named local,
# `xs = [n]` then `super().__init__(xs)`, is a sema error (the base
# initializer must be the first statement).
from tpy import int32


class Base:
    n: int32

    def __init__(self, xs: list[int32]) -> None:
        xs.append(1)
        self.n = len(xs)


class Child(Base):
    # The ctor reject reports the enclosing `def` line, not the offending
    # call, so the annotation sits here.
    def __init__(self, n: int32) -> None:  # tpyc: error(/not yet supported.*call\.arg_shape\.container/)
        super().__init__([n])


def main() -> None:
    print(Child(3).n)


main()
