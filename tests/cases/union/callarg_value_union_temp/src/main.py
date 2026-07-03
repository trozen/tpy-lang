# Member-valued scalar args into value-union slots hoist variant temps at
# the flushable statement positions. Scalars copy -- read-only output intended.
from tpy import Int32, Float64


# Discriminates on Float64 (== float under CPython) so an int arg narrows
# identically on both runtimes.
def take_vu(v: Int32 | Float64) -> Int32:
    if isinstance(v, Float64):
        return -1
    return v


def two(a: Int32 | Float64, b: Int32 | Float64) -> Int32:
    return take_vu(a) + take_vu(b)


def use_decl(k: Int32) -> Int32:
    r = take_vu(k)
    return r


def use_reassign(k: Int32) -> Int32:
    r = 0
    r = take_vu(k + 2)
    return r


def use_stmt(k: Int32) -> Int32:
    take_vu(k)  # discarded result; the temp still evaluates
    return k


def use_two(k: Int32, f: Float64) -> Int32:
    return two(k, f)


def use_float() -> Int32:
    return take_vu(2.5)


def main() -> None:
    print(use_decl(5))
    print(use_reassign(5))
    print(use_stmt(6))
    print(use_two(3, 1.5))
    print(use_float())


main()
