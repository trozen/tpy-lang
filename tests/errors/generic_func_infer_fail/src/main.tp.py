"""Test error when type inference fails for generic functions."""


def identity[T]() -> T:  # tpyc: ok
    pass  # Return type T cannot be inferred from no arguments


identity()  # tpyc: error(/Cannot infer type arguments/)
