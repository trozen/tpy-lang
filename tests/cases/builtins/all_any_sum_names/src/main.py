# all()/any()/sum() over a bare container name route through THIR: the native
# builtin binds the container directly (C++ template), no adapter/span wrap.
from tpy import int32


def check_all(xs: list[bool]) -> bool:
    return all(xs)


def check_any(xs: list[bool]) -> bool:
    return any(xs)


def total(xs: list[int32]) -> int32:
    return sum(xs)


def main() -> None:
    bs: list[bool] = [True, True, False]
    print(all(bs))
    print(any(bs))

    ns: list[int32] = [1, 2, 3]
    print(total(ns))
    print(check_all(bs))
    print(check_any(bs))


main()
