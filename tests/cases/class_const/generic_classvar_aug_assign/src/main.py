# Phase 9: aug-assign on a mutable ClassVar of a generic class. The
# parameterized qname is rendered once for the lvalue and once for the RHS;
# codegen splits non-name-receiver evaluation off so each instantiation's
# slot is mutated in place via `C<int32_t>::counter = ...(C<int32_t>::counter, ...)`.
from typing import ClassVar
from tpy import int32


class C[T]:
    counter: ClassVar[int32] = 0

    def __init__(self) -> None:
        pass


def main() -> None:
    a = C[int32]()
    a.counter += 5  # tpyc: warning(/Assigning to ClassVar 'C.counter' via instance/)
    a.counter += 7  # tpyc: warning(/Assigning to ClassVar 'C.counter' via instance/)
    print(a.counter)


main()
