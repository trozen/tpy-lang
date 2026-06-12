# Mutating __all__ via method calls is a loud error; use '+=' with a
# literal list instead.
__all__ = ["one"]
__all__.append("two")  # tpyc: error(/__all__ method mutation is not supported/)


def one() -> None:
    print("one")


def two() -> None:
    print("two")


def main() -> None:
    one()


main()
