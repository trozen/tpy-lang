# Regression: Box(s) on a str lvalue used to crash codegen with
# `Internal error: PendingStrType should be resolved before codegen`.
from tplib import Box


def main() -> None:
    s = "world"
    b = Box(s)
    print(b.get())

    t: str = "hello"
    b2 = Box(t)
    print(b2.get())


main()
