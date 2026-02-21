# None-seeded variable reassigned inside an if-branch should be
# Optional[T] after the branch, allowing narrowing with `is not None`.
from tpy import Int32, Own

class Box:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v

def make_box() -> Own[Box]:
    return Box(Int32(42))

def test_if_branch() -> None:
    b = None
    if True:
        b = make_box()
    if b is not None:
        print(b.v)

def test_elif_branch() -> None:
    b = None
    x = 1
    if x == 0:
        pass
    elif x == 1:
        b = make_box()
    if b is not None:
        print(b.v)

test_if_branch()
test_elif_branch()
