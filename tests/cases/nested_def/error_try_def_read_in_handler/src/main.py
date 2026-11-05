# A def bound in a `try` body is a local of that body: its handler cannot
# read it (the exception may come before the def ran) -- each part of a `try`
# is a block of its own (docs/LANGUAGE_FEATURES.md, nested `def` "READABLE
# ONLY INSIDE THAT BLOCK"; BUGS.md#nested-def-block-scoped-lambda-read-after-block).
def f(flag: bool) -> int:
    try:
        def h() -> int:
            return 7
        if flag:
            raise ValueError("x")
        return 0
    except ValueError:
        return h()  # tpyc: error(/'h' is not readable after the 'try' block/)


def main() -> None:
    print(f(True))


main()
