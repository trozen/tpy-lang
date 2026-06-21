# Error: a var assigned only in the try body (the handler neither assigns it
# nor terminates), read after the block, is not definitely assigned -> rejected.
from tpy import Int32


def compute() -> Int32:
    return 5


def main() -> None:
    try:
        x = compute()
    except OSError:
        pass
    print(x)  # tpyc: error(/variable 'x' may not be assigned/)


main()
