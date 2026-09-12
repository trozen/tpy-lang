# Parameterized isinstance second arg (`isinstance(x, Pair[int32])`) is
# rejected. The existing isinstance parser path rejects subscripted forms
# uniformly; the parameterized generic alias case is one specific
# instance of that. Tracked separately from the bare-name case in
# error_generic_alias_isinstance because they exercise different
# diagnostic paths.
from tpy import int32

type Pair[T] = tuple[T, T]


def main() -> None:
    p = (int32(1), int32(2))
    if isinstance(p, Pair[int32]):  # tpyc: error(/isinstance.* second argument must be a type name/)
        print(p)


main()
