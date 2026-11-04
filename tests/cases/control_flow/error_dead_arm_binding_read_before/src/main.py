# A read BEFORE a binding in an arm that never runs: the binding still makes
# the name a local of the whole function, so the read is that unassigned local
# (CPython: UnboundLocalError), never the module global.
g = 5


def h() -> None:
    print(g)  # tpyc: error(/'g' is read before it is bound on line 10: .* does not see the module's 'g'/)
    if False:
        g = 1


h()
