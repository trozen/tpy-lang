# Type mismatch on a keyword-only param of a *args function must produce
# a clean tpyc diagnostic, not fall through to a cryptic C++ compile error.
from tpy import Int32


def f(*xs: Int32, name: str = "default") -> str:
    return name


def main() -> None:
    f(Int32(1), name=Int32(99))  # tpyc: error(/argument 'name'/)


main()
