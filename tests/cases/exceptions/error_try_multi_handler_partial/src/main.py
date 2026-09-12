# Error: in a multi-handler try, a var assigned in the try and one handler but
# not the other (non-terminating, no assign) is not definitely assigned -> reject.
from tpy import int32


def risky(n: int32) -> int32:
    if n == 1:
        raise ValueError("v")
    return 10


def main() -> None:
    try:
        x = risky(0)
    except ValueError:
        x = -1
    except OSError:
        pass
    print(x)  # tpyc: error(/variable 'x' may not be assigned/)


main()
