# A T bound by fixed ints of different widths joins them, and the concrete
# overload declared after the generic still wins (twin: int/generic_int_join).
from tpy import int32, int64, dispatch


# the same pair declared generic-first: the concrete one still wins
@dispatch
def which_g[T](x: T, y: T) -> str:
    return "generic"


@dispatch
def which_g(x: int64, y: int64) -> str:
    return "concrete"


def free_function() -> None:
    # declared generic-first: the concrete overload still wins the tie
    print("free which_g", which_g(int32(1), int64(2)))


def main() -> None:
    free_function()


main()
