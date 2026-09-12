# A binop synthesized AFTER sema resolved the body: it carries no resolved
# operator, and the raw-operator leg admits bare NAME operands only, so the
# macro's literal right-hand side falls outside it.
from rawbinmod import to_literal_add
from tpy import int32


def sentinel(a: int32, b: int32) -> int32:
    return 0


@to_literal_add
def combine(a: int32, b: int32) -> int32:
    # The macro rewrites this call into `a + 1`.
    return sentinel(a, b)  # tpyc: error(/stmt\.return:binop\.shape\.\+/)


def main() -> None:
    print(combine(3, 4))


main()
