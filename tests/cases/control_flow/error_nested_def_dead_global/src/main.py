# A `global` in a nested def's dead arm declares the name for the whole nested
# def, so the def takes the refusal a live `global` there takes
# (BUGS.md#nested-def-global-statement-rejects).
g = 1


def f() -> None:
    def inner() -> None:  # tpyc: error(/nesteddef.global_decl/)
        if False:
            global g
        g = 7

    inner()
    print(g)


f()
