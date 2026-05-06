import a

def bar() -> int:
    return 41

def relay() -> int:
    # Calls back into the cycle peer at runtime, exercising the
    # whole-module name binding both ways (a calls b.bar above; b
    # calls a.foo here).
    return a.foo()
