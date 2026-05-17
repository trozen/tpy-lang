# Method bodies hit the same `_gen_var_decl_code` global-declared branch as
# free functions; pins the Ptr[T] / Optional[T] None-reassign codegen for both.
from tpy import Ptr, take_ptr


class Holder:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


_ptr_g: Ptr[Holder] = None
_opt_int_g: int | None = None


class Registry:
    def install(self, h: Ptr[Holder], v: int) -> None:
        global _ptr_g, _opt_int_g
        _ptr_g = h
        _opt_int_g = v

    def clear(self) -> None:
        global _ptr_g, _opt_int_g
        _ptr_g = None
        _opt_int_g = None


def main() -> None:
    h = Holder(7)
    r = Registry()
    r.install(take_ptr(h), 42)
    print(_ptr_g is not None)
    print(_opt_int_g is not None)
    r.clear()
    print(_ptr_g is None)
    print(_opt_int_g is None)


main()
