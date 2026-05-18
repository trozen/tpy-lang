from tpy import Int32
from pkg.b import B


class A:
    b: B

    def __init__(self) -> None:
        self.b = B()

    def value(self) -> Int32:
        return self.b.value()


# Generic record re-exported via pkg/__init__.py; covers the
# template-header branch of _emit_sibling_submodule_fwd_decls
# (`template<typename T> struct Container;`).
class Container[T]:
    item: T

    def __init__(self, item: T) -> None:
        self.item = item

    def get(self) -> T:
        return self.item


def f(n: Int32) -> Int32:
    return n * Int32(2)
