# Upcast to generic parent: IntContainer -> Container[Int32]
from tpy import Int32

class Container[T]:
    value: T
    def __init__(self, value: T) -> None:
        self.value = value

class IntContainer(Container[Int32]):
    def __init__(self, value: Int32) -> None:
        super().__init__(value)

def read_container(c: Container[Int32]) -> None:
    print(c.value)

def main() -> None:
    ic: IntContainer = IntContainer(Int32(42))
    # Value upcast to generic parent
    c: Container[Int32] = ic
    print(c.value)
    # Param passing
    read_container(ic)

main()
