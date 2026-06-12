# Or-patterns with a None alternative on an Optional subject: `None | _`
# covers both sides (exhaustive, no warning); `None | 5` covers None only.
from tpy import Int32


def all_arm(v: Int32 | None) -> None:
    match v:  # tpyc: ok
        case None | _:
            print("any")


def none_or_five(v: Int32 | None) -> None:
    match v:
        case None | 5:
            print("none-or-five")
        case _:
            print("other")


def main() -> None:
    all_arm(1)
    all_arm(None)
    none_or_five(None)
    none_or_five(5)
    none_or_five(2)


main()
