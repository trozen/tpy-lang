# Union with None member uses std::monostate in codegen
from tpy import int32


def accept(x: int32 | str | None) -> None:
    pass


def test() -> None:
    a: int32 | str | None = int32(42)
    b: int32 | str | None = "hello"
    accept(a)
    accept(b)
    print("ok")

test()
