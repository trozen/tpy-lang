# Walrus variable from short-circuit RHS used after if -- must error
# (`x` is a parameter, so sema does not decide the `if` test); the
# definite-assignment rule, docs/LANGUAGE_FEATURES.md "Definite-Assignment
# Analysis".
def test(x: int) -> None:
    if (x > 5) and (y := x * 2) > 15:
        print(y)
    print(y)  # tpyc: error(/may not be assigned/)

test(10)
