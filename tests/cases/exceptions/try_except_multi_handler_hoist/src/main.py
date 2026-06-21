# Multi-handler try/except: a var assigned in the try body AND in every handler
# is definitely-assigned on all paths, so it hoists and reads after the block.
from tpy import Int32


def risky(n: Int32) -> Int32:
    if n == 1:
        raise ValueError("v")
    if n == 2:
        raise OSError("o")
    return 10


def run(n: Int32) -> Int32:
    try:
        x = risky(n)
    except ValueError:
        x = -1
    except OSError:
        x = -2
    return x


def main() -> None:
    print(run(0))
    print(run(1))
    print(run(2))


main()
