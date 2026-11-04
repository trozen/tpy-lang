def check(x: int) -> None:  # a parameter: sema does not decide the assert
    assert x > 0, 42  # tpyc: error(/assert message must be a string/)


check(1)
