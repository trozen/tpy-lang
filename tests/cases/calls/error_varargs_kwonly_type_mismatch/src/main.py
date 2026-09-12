# Type mismatch on a keyword-only param of a *args function must produce
# a clean tpyc diagnostic, not fall through to a cryptic C++ compile error.
from tpy import int32


def f(*xs: int32, name: str = "default") -> str:
    return name


def main() -> None:
    f(int32(1), name=int32(99))  # tpyc: error(/argument 'name'/)


main()
