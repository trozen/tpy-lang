# Tuple unpacking with Own[T] element (should unwrap)
from tpy import Int32, Own

class Pair:
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def make() -> tuple[Own[Pair], Int32]:
    return (Pair(Int32(1), Int32(2)), Int32(99))

def main() -> None:
    p, n = make()
    print(p.x)
    print(p.y)
    print(n)

main()
