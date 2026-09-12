# `case None as x:` is rejected on an Optional subject: NoneType is
# monostate, there is no value to bind (declared CPython divergence).
from tpy import int32


def f(v: int32 | None) -> None:
    match v:
        case None as x:  # tpyc: error(/'as' binding not allowed on 'case None:'/)
            print("none")
        case _:
            print("other")


def main() -> None:
    f(None)


main()
