# Test that inplace dunders reject Own[T] return (must return self, not a new value)
from tpy import Int32, Own

class Vec:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def __iadd__(self, other: Vec) -> Own[Vec]:  # tpyc: error(must return self)
        return Vec(self.x + other.x)

def main() -> None:
    v = Vec(Int32(1))

main()
