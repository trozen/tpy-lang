# Parameterized isinstance second arg (`isinstance(x, Pair[Int32])`) is
# rejected. The existing isinstance parser path rejects subscripted forms
# uniformly; the parameterized generic alias case is one specific
# instance of that. Tracked separately from the bare-name case in
# error_generic_alias_isinstance because they exercise different
# diagnostic paths.
from tpy import Int32

type Pair[T] = tuple[T, T]


def main() -> None:
    p = (Int32(1), Int32(2))
    if isinstance(p, Pair[Int32]):  # tpyc: error(/isinstance.* second argument must be a type name/)
        print(p)


main()
