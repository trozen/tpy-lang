# A parameter default read off a module (`consts.limit`) must name a
# module-level Final constant, as a bare imported name must: the restriction
# in docs/LANGUAGE_FEATURES.md, "Default parameter values".
from tpy import int32
import consts


def scaled(n: int32 = consts.limit) -> int32:  # tpyc: error(/Default parameter value 'consts.limit' must be a module-level Final\[T\] constant/)
    return n * 2


def main() -> None:
    print(scaled())


main()
