# An `except` clause naming a nested def of the function: the name is the
# function's local, a function, which names no exception class (CPython
# raises TypeError when the clause is tested).
from tpy import int32


def main() -> None:
    def ValueError() -> int32:
        return 0
    try:
        print(ValueError())
    # The clause reads the nested def, not the builtin class.
    except ValueError:  # tpyc: error(/'ValueError' is a local variable here, not a type/)
        print("caught")


main()
