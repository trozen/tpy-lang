# Union type annotations: declarations, local variables, and function calls
from tpy import Int32


def accept_two(x: Int32 | str) -> None:
    pass


def accept_three(x: Int32 | str | bool) -> None:
    pass


def test() -> None:
    a: Int32 | str = Int32(10)
    b: Int32 | str = "hello"
    accept_two(a)
    accept_two(b)
    c: Int32 | str | bool = True
    accept_three(c)
    print("ok")

test()
