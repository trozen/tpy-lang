# None-seeded variable reassigned inside a while loop should be
# Optional[T] after the loop, allowing narrowing with `is not None`.
from tpy import Int32, Own

class Box:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v

def make(i: Int32) -> Own[Box]:
    return Box(i)

def test_while() -> None:
    result = None
    i = Int32(0)
    while i < Int32(3):
        result = make(i)
        i += Int32(1)
    if result is not None:
        print(result.v)

test_while()
