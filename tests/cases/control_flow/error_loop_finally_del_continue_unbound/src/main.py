# After a loop, a name the loop deletes may not be assigned: a `continue`
# that leaves through a `finally` deleting what it saw bound reaches the
# loop's exhaustion exit (CPython: UnboundLocalError).
from tpy import int32


def f() -> int32:
    for i in range(1):
        x = 4
        try:
            continue
        finally:
            del x
    # The subject: the `finally` deleted `x` on the way out.
    return x  # tpyc: error(/variable 'x' may not be assigned at this point/)


def main() -> None:
    print(f())


main()
