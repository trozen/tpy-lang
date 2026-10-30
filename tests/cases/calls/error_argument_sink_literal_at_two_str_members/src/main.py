# A str literal at a union with two members that both take it (str and
# String): which member it builds is ambiguous, so the argument is refused.
from tpy import String


def k(u: str | String) -> int:
    return 1


def main() -> None:
    print(k("a"))  # tpyc: error(/call\.arg_shape\.union/)


main()
