# Generic + non-union + self-referential alias. Distinct from
# error_generic_alias_recursive (which uses a union body) and from
# error_generic_alias_recursive_non_union (which is non-generic). The
# inner Bag[T] self-reference is detected via the AliasRef placeholder;
# being generic + recursive, it hits the Phase-1 sema rejection.
from tpy import Int32

type Bag[T] = list[Bag[T]]  # tpyc: error(/Generic recursive type aliases are not yet supported/)


def main() -> None:
    pass


main()
