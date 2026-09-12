# Old-style type alias with builtin type names
from tpy import int32


Num = int | bool


def show_num(x: Num) -> None:
    if isinstance(x, bool):
        print("bool")
    else:
        print("int")


def main() -> None:
    a: Num = 42
    b: Num = True
    show_num(a)
    show_num(b)

main()
