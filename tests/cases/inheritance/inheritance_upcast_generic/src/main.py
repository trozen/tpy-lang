# Upcast to generic parent: IntContainer -> Container[int32]
from tpy import int32

class Container[T]:
    value: T
    def __init__(self, value: T) -> None:
        self.value = value

class IntContainer(Container[int32]):
    def __init__(self, value: int32) -> None:
        super().__init__(value)

def read_container(c: Container[int32]) -> None:
    print(c.value)

def main() -> None:
    ic: IntContainer = IntContainer(int32(42))
    # Value upcast to generic parent
    c: Container[int32] = ic  # tpyc: warning(/upcast narrows 'IntContainer' to 'Container\[int32\]'/)
    print(c.value)
    # Param passing
    read_container(ic)

main()
