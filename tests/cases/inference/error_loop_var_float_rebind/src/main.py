# Rebinding an int loop variable to a float is refused: the loop variable has
# the element type, and a local has one numeric type.

def walk() -> None:
    for i in range(2):
        i = 2.5  # tpyc: error(/'i' has type int32 and is bound to float here/)
        print(i)

walk()
