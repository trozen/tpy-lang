# __all__ += without a prior __all__ assignment is a loud error
# (NameError in CPython too).
__all__ += ["one"]  # tpyc: error(/__all__ \+= without a prior __all__ assignment/)


def one() -> None:
    print("one")


def main() -> None:
    one()


main()
