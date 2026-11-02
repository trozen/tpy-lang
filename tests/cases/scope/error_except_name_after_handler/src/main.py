# The end of an `except ... as e` handler deletes `e`, also when `e` was bound
# before the `try`: the read after the handler is CPython's
# UnboundLocalError, never the earlier value.
from tpy import int32


def main() -> None:
    e = 7
    try:
        raise ValueError("x")
    except ValueError as e:
        print("caught")
    # The read after the handler that unbound `e`.
    print(e)  # tpyc: error(/variable 'e' may not be assigned at this point/)


main()
