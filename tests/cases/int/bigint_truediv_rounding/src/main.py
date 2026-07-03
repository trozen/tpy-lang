# int/int true division: correctly rounded (single rounding of the exact
# quotient), "division by zero" message, signed zeros, OverflowError on overflow.
def main() -> None:
    a: int = -86399999913600000000
    b: int = 1000000
    print(a / b)              # -86399999913600.0 (was ...599.98 double-rounded)

    c: int = (1 << 60) + (1 << 7)
    d: int = 3
    print(c / d)              # 3.843071682022824e+17

    e: int = (1 << 53) + 1
    f: int = 1
    print(e / f)              # ties-to-even -> 9007199254740992.0

    g: int = 10**23 + 1
    h: int = 10**22
    print(g / h)              # 10.0

    z: int = 0
    w: int = -5
    print(z / w)              # -0.0 (result zero keeps the quotient's sign)

    tiny: int = 1
    huge: int = 10**330
    print(tiny / huge)        # 0.0 (underflow)
    print(-tiny / huge)       # -0.0

    big: int = 10**400
    try:
        print(big / d)
    except OverflowError as ex:
        print("overflow:", ex)

    try:
        print(tiny / z)
    except ZeroDivisionError as ex:
        print("zerodiv:", ex)


main()
