# Inverse of block_local_reassign_after: when a loop-body local is READ after
# the loop it is pre-declared at function scope and the body assigns into it.
# Revoking the body's declarations must not disturb that -- the pre-declared
# name is established before the block, so it outlives it.


def read_after() -> int:
    for i in range(3):
        n = i
    return n


def read_then_assign() -> int:
    # The read on the RHS sees the loop's last value; the assignment targets
    # the same local.
    for i in range(3):
        n = i
    n = n + 1
    return n


def augmented_after() -> int:
    for i in range(3):
        n = i
    n += 1
    return n


def declared_before() -> int:
    # Already function-scoped before the loop: the body assigns, never declares.
    n = 0
    for i in range(3):
        n = i
    n = 9
    return n


def read_in_later_block() -> int:
    # Read from inside a SIBLING block after the declaring one.
    for i in range(3):
        n = i
    total = 0
    for _ in range(2):
        total += n
    return total


def read_after_while() -> int:
    # `while` took the same scope-revoke widening as `for`, so it needs the
    # same over-trigger guard: a body-declared name read after the loop must
    # still be pre-declared at function scope rather than re-declared.
    i = 0
    n = 0
    while i < 3:
        n = i
        i += 1
    return n


def sibling_loops_reuse_name() -> int:
    # Two sibling loops each declaring the same name are independent locals;
    # the second must still be able to declare it.
    for i in range(2):
        n = i + 1
        print(n)
    for i in range(2):
        n = i + 10
        print(n)
    return n


def main() -> None:
    print(read_after())
    print(read_then_assign())
    print(augmented_after())
    print(declared_before())
    print(read_in_later_block())
    print(read_after_while())
    print(sibling_loops_reuse_name())


main()
