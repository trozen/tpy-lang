# Chained and literal-on-LHS comparisons against BigInt: derived operators
# (<=, >, >=, !=) emit the bare C++ operator, so either operand side converts.
def main() -> None:
    x: int = 5

    if 1 <= x <= 12:
        print("in range")
    if not (6 <= x <= 12):
        print("below")

    lo: int = 1
    hi: int = 12
    if lo <= x <= hi:
        print("var bounds")

    b = 1 <= x
    print(b)
    print(20 > x)
    print(1 >= x)
    print(3 != x)

    n: int = 3
    while 0 < n <= 3:
        n -= 1
    print(n)


main()
