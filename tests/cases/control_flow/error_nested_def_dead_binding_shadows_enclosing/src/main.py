# A nested def's own binding, even in an arm that never runs, makes the name
# its local: its read is that unassigned local (CPython: UnboundLocalError),
# never the enclosing function's binding of the same name.
def outer() -> None:
    g = 5

    def inner() -> None:
        if False:
            g = 3
        print(g)  # tpyc: error(/variable 'g' may not be assigned at this point/)

    inner()


outer()
