# list comprehension rejects subclass element type (covers _analyze_elem_comprehension path)
# Storing Child in list[Base] would silently slice objects in C++
from tpy import Int32


class Base:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


class Child(Base):
    extra: Int32

    def __init__(self, v: Int32, e: Int32) -> None:
        super().__init__(v)
        self.extra = e


def main() -> None:
    children: list[Child] = []
    items: list[Base] = [c for c in children]  # tpyc: error(/List comprehension element.*Child.*incompatible.*Base/)


main()
