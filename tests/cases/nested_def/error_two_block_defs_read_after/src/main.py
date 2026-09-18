# TWO nested `def`s in one block: each one's binding retires with the block
# independently of its sibling, so reading the FIRST after the block is
# rejected exactly like reading the last. Lifting the limitation means giving
# the callable a slot at function scope --
# BUGS.md#nested-def-block-scoped-lambda-read-after-block.
from tpy import int32


def scale(x: int32) -> int32:
    return x + 1


def main() -> None:
    flag = True
    if flag:
        def scale(x: int32) -> int32:
            return x + 100

        def offset(x: int32) -> int32:
            return x + 200

        print(offset(scale(1)))
    else:
        def scale(x: int32) -> int32:
            return x + 300

        def offset(x: int32) -> int32:
            return x + 400

        print(offset(scale(1)))

    # the FIRST def of the block -- its ownership must survive its sibling
    print(scale(1))  # tpyc: error(/'scale' is not readable after the 'if' block/)


main()
