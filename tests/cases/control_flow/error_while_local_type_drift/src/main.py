# The `while` sibling of error_block_local_type_drift. A while body's locals
# never reach the pending-loop-var table, so the one-type-per-local check reads
# the per-function record of the name's first declaration instead -- a separate
# path that needs its own guard.


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
