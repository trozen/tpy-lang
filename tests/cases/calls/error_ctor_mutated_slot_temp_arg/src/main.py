# The ctor family's mutated-slot rule, stated on the shape it exists for: a
# mutable `list[int32]&` parameter cannot bind a TEMPORARY, so a copy rvalue
# at a mutated constructor slot keeps rejecting (a FIELD read at the same slot
# is an lvalue and compiles -- calls/ctor_mutated_slot_field_arg).
from tpy import copy, int32


class W:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1]


class ListTaker:
    n: int32

    def __init__(self, xs: list[int32]) -> None:
        xs.append(9)
        self.n = len(xs)


def main() -> None:
    w = W()
    lt = ListTaker(copy(w.items))  # tpyc: error(/not yet supported.*ctor_arg/)
    print(lt.n)


main()
