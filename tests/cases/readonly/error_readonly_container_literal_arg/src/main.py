# A NON-EMPTY container literal at a free function's `readonly[list]` parameter
# is refused (the EMPTY literal binds inline, and so does the record-method
# twin). Binding the brace inline is only safe when the callee does not lend
# the parameter back, and that fact is not recorded for every callee
# (BUGS.md#readonly-container-literal-arg-rejected). Workaround: bind the
# literal to a local first.
from tpy import int32, readonly


def total(xs: readonly[list[int32]]) -> int32:
    n = 0
    for x in xs:
        n += x
    return n


def main() -> None:
    # the literal at the readonly slot is the reject
    print(total([1, 2, 3]))  # tpyc: error(/not yet supported/)


main()
