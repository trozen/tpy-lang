# Python evaluates the return expression BEFORE the finally body runs --
# and still evaluates it (side effects included) when the finally's own
# return overrides the pending value.
def f() -> int:
    x = 1
    try:
        return x
    finally:
        x = 2


def bump() -> int:
    print("bump")
    return 10


def g() -> int:
    try:
        return bump()
    finally:
        return 99


def main() -> None:
    print(f())
    print(g())


main()
