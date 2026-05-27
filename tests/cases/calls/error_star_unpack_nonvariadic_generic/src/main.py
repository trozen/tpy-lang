# *unpack into a NON-variadic generic function must be rejected cleanly.
# Generic inference may element-extract the unpack as evidence, so the
# non-variadic typecheck gate in _analyze_generic_function_call is what
# rejects the misuse (previously crashed with "Unknown expression type").
from tpy import Int32


def one_arg[T](a: T) -> T:
    return a


def main() -> None:
    xs: list[Int32] = [1]
    print(one_arg(*xs))  # tpyc: error(/does not accept \*args/)


main()
