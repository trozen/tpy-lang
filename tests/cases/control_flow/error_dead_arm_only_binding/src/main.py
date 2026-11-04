# A name bound only in a dead arm is still a local of the function (Python's
# scoping is lexical): the read after it is unassigned, never the global.
x = 7


def f() -> None:
    if False:
        x = 3
    print(x)  # tpyc: error(/variable 'x' may not be assigned at this point/)


f()
