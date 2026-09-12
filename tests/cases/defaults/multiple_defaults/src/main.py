# Multiple consecutive default parameters, called with varying arg counts
from tpy import int32

def create(name: str, width: int32 = int32(100), height: int32 = int32(50), visible: bool = True) -> None:
    print(name)
    print(width)
    print(height)
    print(visible)

def main() -> None:
    create("a")
    create("b", int32(200))
    create("c", int32(200), int32(300))
    create("d", int32(200), int32(300), False)

main()
