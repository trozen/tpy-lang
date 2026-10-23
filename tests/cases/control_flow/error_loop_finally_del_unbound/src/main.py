# After a loop, a name the loop deletes may not be assigned: a `break` (or a
# `continue`) can leave through a `finally` that deletes what the edge saw
# bound (CPython: UnboundLocalError).
from tpy import int32


def f() -> int32:
    while True:
        x = 4
        try:
            break
        finally:
            del x
    # The subject: the `finally` deleted `x` on the way out.
    return x  # tpyc: error(/variable 'x' may not be assigned at this point/)


def main() -> None:
    print(f())


main()
