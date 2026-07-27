# A local first declared in a loop BODY and assigned after the loop is the same
# Python local, so it carries one type: the block boundary must not let the name
# silently pick up a second type the way a fresh declaration would.


def f() -> int:
    for i in range(3):
        n = i
        print(n)
    n = "hello"   # tpyc: error(/Type mismatch in reassignment to 'n'/)
    print(n)
    return 0


f()
