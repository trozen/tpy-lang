# Optional subject: guarded capture binds the full T | None before the guard
# runs (guard may read and narrow it); a failed guard falls through to the
# None arm and the wildcard.
from tpy import int32


def classify(v: int32 | None) -> None:
    match v:
        case x if x is not None and x > 5:
            print("big", x)
        case None:
            print("none")
        case _:
            print("small")


def main() -> None:
    classify(7)
    classify(None)
    classify(1)


main()
