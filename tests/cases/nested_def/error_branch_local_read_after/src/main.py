# A nested `def` bound inside a block and read AFTER the block. The rule is
# whole-block, not per-path: such a `def` declares its callable for that block
# alone, so the read has nothing to reach even here, where BOTH arms bind the
# name -- and Python makes the name a local of the whole enclosing scope, so
# the module `helper` is not a candidate either. The single-arm spelling (only
# the `if` binds) and the `for` / `while` / `try` / `with` / `match` positions
# reject the same way; a read INSIDE the block compiles (the sections of
# nested_def/name_collides_with_function). Lifting the limitation means giving
# the callable a slot at function scope --
# BUGS.md#nested-def-block-scoped-lambda-read-after-block.
from tpy import int32


def helper(x: int32) -> int32:
    return x + 1


def main() -> None:
    flag = True
    if flag:
        def helper(x: int32) -> int32:
            return x + 100
    else:
        def helper(x: int32) -> int32:
            return x + 200

    # every path bound `helper`, but each binding belongs to its own arm
    print(helper(1))  # tpyc: error(/'helper' is not readable after the 'if' block/)


main()
