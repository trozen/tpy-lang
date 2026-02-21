# Union with None member uses std::monostate in codegen
from tpy import Int32


def accept(x: Int32 | str | None) -> None:
    pass


def test() -> None:
    a: Int32 | str | None = Int32(42)
    b: Int32 | str | None = "hello"
    accept(a)
    accept(b)
    print("ok")

test()
