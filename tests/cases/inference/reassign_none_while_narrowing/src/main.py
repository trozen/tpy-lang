# None-seeded variable reassigned inside a while loop should be
# Optional[T] after the loop, allowing narrowing with `is not None`.
from tpy import int32, Own

class Box:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v

def make(i: int32) -> Own[Box]:
    return Box(i)

def test_while() -> None:
    result = None
    i = int32(0)
    while i < int32(3):
        result = make(i)
        i += int32(1)
    if result is not None:
        print(result.v)

test_while()
