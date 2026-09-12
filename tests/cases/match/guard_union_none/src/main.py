# Union-with-None subject: a guard on one arm routes the whole match through
# the guarded union path, which must still support `case None:`.
from tpy import int32


class A:
    a: int32

    def __init__(self) -> None:
        self.a = 1


class B:
    b: int32

    def __init__(self) -> None:
        self.b = 2


def f(v: A | B | None, flag: bool) -> None:
    match v:
        case A() if flag:
            print("is A flagged")
        case None:
            print("is none")
        case _:
            print("other")


def main() -> None:
    f(A(), True)
    f(A(), False)
    f(None, True)
    f(B(), True)


main()
