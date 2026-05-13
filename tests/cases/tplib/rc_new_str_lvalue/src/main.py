# Regression: Rc.new(s) on a str lvalue used to crash codegen with
# `Internal error: PendingStrType should be resolved before codegen`.
from tplib import Rc


def main() -> None:
    s = "world"
    r = Rc.new(s)
    print(r.get())

    t: str = "hello"
    r2 = Rc.new(t)
    print(r2.get())


main()
