# A guarded union arm alongside an or-alternative containing a wildcard: the
# guarded tier distributes alternatives per variant index and has no default
# block to fold the wildcard into. The guard also makes the Cat arm
# conditional, so the match reads as non-exhaustive.
from tpy import Int32


class Cat:
    legs: Int32

    def __init__(self) -> None:
        self.legs = 4


class Dog:
    legs: Int32

    def __init__(self) -> None:
        self.legs = 4


def kind(a: Cat | Dog | None, ok: bool) -> str:
    match a:  # tpyc: error(/stmt\.match/)
        case Cat() if ok:
            return "cat"
        # The wildcard alternative has no variant index of its own.
        case Dog() | None | _:
            return "rest"
    return "no"


def main() -> None:
    print(kind(None, True))


main()
