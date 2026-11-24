# A str literal at `str | String` lands its own type, str, as it does at
# the scalar parameter `k(u: str)`.
from tpy import String


def k(u: str | String) -> bool:
    # The str member holds it (CPython's `String` is `str` itself, so only
    # the str test answers alike).
    return isinstance(u, str)


def main() -> None:
    print(k("a"))  # tpyc: ok


main()
