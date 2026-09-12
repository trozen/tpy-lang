# Union type annotations: declarations, local variables, and function calls
from tpy import int32


def accept_two(x: int32 | str) -> None:
    pass


def accept_three(x: int32 | str | bool) -> None:
    pass


def test() -> None:
    a: int32 | str = int32(10)
    b: int32 | str = "hello"
    accept_two(a)
    accept_two(b)
    c: int32 | str | bool = True
    accept_three(c)
    print("ok")

test()
