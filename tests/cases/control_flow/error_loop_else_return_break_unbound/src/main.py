# A loop whose `else` returns is left only through its breaks, which leave it
# live: a name only the other branch of the `if` binds may not be assigned.
from tpy import int32


def f(c: bool) -> int32:
    if c:
        while True:
            break
        else:
            return 0
    else:
        w = 42
    # The subject: `w` is unbound on the path through the `break`.
    return w  # tpyc: error(/variable 'w' may not be assigned at this point/)


def main() -> None:
    print(f(True))


main()
