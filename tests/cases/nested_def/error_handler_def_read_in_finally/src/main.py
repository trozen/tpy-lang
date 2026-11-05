# A def bound in an `except` handler is a local of that handler: the
# `finally` (which runs on every path) cannot read it -- each part of a `try`
# is a block of its own (docs/LANGUAGE_FEATURES.md, nested `def` "READABLE
# ONLY INSIDE THAT BLOCK"; BUGS.md#nested-def-block-scoped-lambda-read-after-block).
def f() -> int:
    try:
        raise ValueError("x")
    except ValueError:
        def h() -> int:
            return 7
    finally:
        print(h())  # tpyc: error(/'h' is not readable after the 'try' block/)
    return 0


def main() -> None:
    f()


main()
