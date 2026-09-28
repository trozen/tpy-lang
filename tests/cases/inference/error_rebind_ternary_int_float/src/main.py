# An inferred float local's type hints its rebind without declaring it float, so
# a ternary picking an int or a float is refused rather than converted.

def rebind(c: bool) -> None:
    x = 0.5
    print(x)
    # a declared `x: float` would convert the int arm; the inferred local does not
    x = 1 if c else 2.5  # tpyc: error(/conditional expression mixes int and float.*write 1\.0 instead of 1/)
    print(x)

rebind(True)
