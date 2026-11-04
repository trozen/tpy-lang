# A nested def's read BEFORE its own dead binding of the name is that
# unassigned local (CPython: UnboundLocalError), never the enclosing
# function's `g`: the read-before twin of
# error_nested_def_dead_binding_shadows_enclosing.
def outer() -> None:
    g = 5

    def inner() -> None:
        print(g)  # tpyc: error(/'g' is read before it is bound on line 11: .* does not see the local 'g' of 'outer'/)
        if False:
            g = 3

    inner()


outer()
