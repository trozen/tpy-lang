# `raise errs.Err[int](1)` checks its written type arguments against the class
# as the expression does: Err is not generic.
import errs


def main() -> None:
    raise errs.Err[int](1)  # tpyc: error(/expects 0 type arguments, got 1/)


main()
