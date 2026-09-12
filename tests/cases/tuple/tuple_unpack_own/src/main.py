# Tuple unpacking with Own[T] element (should unwrap)
from tpy import int32, Own

class Pair:
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def make() -> tuple[Own[Pair], int32]:
    return (Pair(int32(1), int32(2)), int32(99))

def main() -> None:
    p, n = make()
    print(p.x)
    print(p.y)
    print(n)

main()
