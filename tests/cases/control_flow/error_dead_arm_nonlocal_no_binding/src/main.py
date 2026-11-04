# A `nonlocal` in an arm that never runs is still checked: a name no
# enclosing function binds is an error (CPython: SyntaxError).
def f() -> None:
    def inner() -> None:
        if False:
            nonlocal zz  # tpyc: error(/No binding for nonlocal 'zz'/)
        print(1)

    inner()


f()
