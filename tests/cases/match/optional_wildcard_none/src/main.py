# Optional subject: wildcard and literal arms; `case _:` must also match None
# (the None side may not be silently dropped by the has_value partition).
from tpy import int32


def label(v: int32 | None) -> None:
    match v:
        case 5:
            print("five")
        case _:
            print("other")


def pick(v: int32 | None) -> int32:
    match v:
        case 5:
            return 50
        case _:
            return 99


def main() -> None:
    label(5)
    label(1)
    label(None)
    print(pick(5))
    print(pick(None))


main()
