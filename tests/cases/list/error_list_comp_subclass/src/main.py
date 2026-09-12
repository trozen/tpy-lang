# list comprehension rejects subclass element type (covers _analyze_elem_comprehension path)
# Storing Child in list[Base] would silently slice objects in C++
from tpy import int32


class Base:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


class Child(Base):
    extra: int32

    def __init__(self, v: int32, e: int32) -> None:
        super().__init__(v)
        self.extra = e


def main() -> None:
    children: list[Child] = []
    items: list[Base] = [c for c in children]  # tpyc: error(/List comprehension element.*Child.*incompatible.*Base/)


main()
