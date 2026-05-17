# Conditional Ptr[T] / Optional[T] reassignment under `global` -- pins the
# codegen branch-shape that `global_keyword_write_branch` covers for BigInt only.
from tpy import Ptr, take_ptr


class Holder:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


_ptr_g: Ptr[Holder] = None
_opt_int_g: int | None = None


def update(h: Ptr[Holder], v: int, attach: bool) -> None:
    global _ptr_g, _opt_int_g
    if attach:
        _ptr_g = h
        _opt_int_g = v
    else:
        _ptr_g = None
        _opt_int_g = None


def main() -> None:
    h = Holder(7)
    update(take_ptr(h), 42, True)
    print(_ptr_g is not None)
    print(_opt_int_g is not None)
    update(take_ptr(h), 0, False)
    print(_ptr_g is None)
    print(_opt_int_g is None)


main()
