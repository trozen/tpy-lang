"""Test that generic inference fails when no __init__ params exist."""


class Empty[T]:
    pass


# Should fail - cannot infer T with no arguments
empty = Empty()  # tpyc: error(/Cannot infer type arguments/)
