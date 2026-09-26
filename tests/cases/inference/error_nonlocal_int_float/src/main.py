# A closure's `nonlocal` write of a float into an inferred int local of the
# enclosing function is refused: a local has one numeric type.

def outer() -> None:
    x = 0
    def inner() -> None:
        nonlocal x
        x = 1.5  # tpyc: error(/'x' has type int32 and is bound to float here.*bind the float value to a new name/)
    inner()
    print(x)

outer()
