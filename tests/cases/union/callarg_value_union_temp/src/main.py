# Member-valued scalar args into value-union slots hoist variant temps at
# the flushable statement positions. Scalars copy -- read-only output intended.
from tpy import int32, float64


# Discriminates on float64 (== float under CPython) so an int arg narrows
# identically on both runtimes.
def take_vu(v: int32 | float64) -> int32:
    if isinstance(v, float64):
        return -1
    return v


def two(a: int32 | float64, b: int32 | float64) -> int32:
    return take_vu(a) + take_vu(b)


def use_decl(k: int32) -> int32:
    r = take_vu(k)
    return r


def use_reassign(k: int32) -> int32:
    r = 0
    r = take_vu(k + 2)
    return r


def use_stmt(k: int32) -> int32:
    take_vu(k)  # discarded result; the temp still evaluates
    return k


def use_two(k: int32, f: float64) -> int32:
    return two(k, f)


def use_float() -> int32:
    return take_vu(2.5)


def main() -> None:
    print(use_decl(5))
    print(use_reassign(5))
    print(use_stmt(6))
    print(use_two(3, 1.5))
    print(use_float())


main()
