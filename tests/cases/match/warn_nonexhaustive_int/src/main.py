# int subject with only literal arms: exhaustiveness cannot be proven, so the
# match must warn and fall through (no std::unreachable on the no-match path).
from tpy import int32


def f(n: int32) -> int32:
    match n:  # tpyc: warning(/non-exhaustive match.*no unconditional catch-all/)
        case 1:
            return 10
        case 2:
            return 20
    return 0


def main() -> None:
    print(f(1))
    print(f(2))
    print(f(7))


main()
