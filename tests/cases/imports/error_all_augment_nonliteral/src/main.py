# __all__ += with a non-literal value is a loud error (dynamic export
# sets cannot be resolved at compile time).
extra = ["two"]
__all__ = ["one"]
__all__ += extra  # tpyc: error(/__all__ \+= value is not a compile-time literal/)


def one() -> None:
    print("one")


def main() -> None:
    one()


main()
