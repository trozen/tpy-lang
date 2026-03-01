# Multiple consecutive default parameters, called with varying arg counts
from tpy import Int32

def create(name: str, width: Int32 = Int32(100), height: Int32 = Int32(50), visible: bool = True) -> None:
    print(name)
    print(width)
    print(height)
    print(visible)

def main() -> None:
    create("a")
    create("b", Int32(200))
    create("c", Int32(200), Int32(300))
    create("d", Int32(200), Int32(300), False)

main()
