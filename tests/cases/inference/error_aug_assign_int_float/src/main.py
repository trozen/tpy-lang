# An int accumulator that `+=` makes float is refused, with the float seed as the fix.

def mean(n: int) -> None:
    total = 0
    for i in range(n):
        total += 0.5  # tpyc: error(/'total' is bound to int at line 4 and '\+=' makes it float here.*write 0\.0 instead of 0/)
    print(total)

mean(0)
