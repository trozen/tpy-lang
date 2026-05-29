# Generic + non-union + self-referential alias. A recursive alias must use a
# union body so the compiler can emit a variant-backed wrapper struct; a bare
# non-union self-reference (whether generic like this or non-generic, see
# error_generic_alias_recursive_non_union) has no wrapper path and is rejected
# at parse-resolution.
from tpy import Int32

type Bag[T] = list[Bag[T]]  # tpyc: error(/must use a union form/)


def main() -> None:
    pass


main()
