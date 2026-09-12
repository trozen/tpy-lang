# Optional subject: a bare capture matches None and binds the full T | None
# (CPython semantics); with a preceding `case None:` arm it narrows to T.
from tpy import int32


def full(v: int32 | None) -> None:
    match v:
        case x:
            if x is None:
                print("got none")
            else:
                print(x + 1)


def narrowed(v: int32 | None) -> None:
    match v:
        case None:
            print("none arm")
        case x:
            print(x * 2)


def main() -> None:
    full(4)
    full(None)
    narrowed(10)
    narrowed(None)


main()
