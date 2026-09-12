# Optional subject: a capture that matches None binds T | None; rebinding an
# existing T-typed local to the wider type is rejected with a fix hint.
from tpy import int32


def f(v: int32 | None) -> None:
    x = 100
    print(x)
    match v:
        case x if x is not None:  # tpyc: error(/match capture 'x' binds the full Optional/)
            print("value")
        case _:
            print("none")


def main() -> None:
    f(1)


main()
