# The `while` sibling of error_block_local_type_drift: one local has ONE type,
# the join of every binding to it anywhere in the function, so a body binding
# and a later one after the loop have to agree. `str` does not join `int32`,
# and the mismatch is reported at the binding that broke it.


def f(k: int) -> int:
    i = 0
    while i < k:
        n = i
        print(n)
        i += 1
    n = "hello"   # tpyc: error(/Type mismatch in reassignment to 'n'/)
    print(n)
    return 0


f(2)
