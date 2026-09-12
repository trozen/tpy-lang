# Walrus operator (:=) in various positions: if, while, and/or chains, expressions
from tpy import int32

def get_opt(x: int32) -> int32 | None:
    if x > 0:
        return x * 10
    return None

def test_if_condition() -> None:
    items = [1, 2, 3, 4, 5]
    if (n := len(items)) > 3:  # tpyc: ok
        print(n)

def test_optional_narrowing() -> None:
    if (val := get_opt(3)) is not None:  # tpyc: ok
        result = val + 5
        print(result)
    if (val2 := get_opt(-1)) is not None:  # tpyc: ok
        print("unreachable")
    else:
        print("none")

def test_and_chain() -> None:
    x: int32 = 10
    if (y := x * 2) > 15 and (z := y + 1) > 20:  # tpyc: ok
        print(y, z)

def test_while_loop() -> None:
    values = [10, 20, 30, 0, 40]
    i: int32 = 0
    while (v := values[i]) != 0:  # tpyc: ok
        print(v)
        i += 1

def test_expression_position() -> None:
    y = (x := 5) + 1  # tpyc: ok
    print(x, y)

def test_multiple_walrus() -> None:
    a = (p := 3) + (q := 7)  # tpyc: ok
    print(p, q, a)

def test_reuse_walrus_target() -> None:
    # Same walrus target used twice -- must not produce duplicate C++ declarations
    if (val := get_opt(3)) is not None:  # tpyc: ok
        print(val)
    if (val := get_opt(5)) is not None:  # tpyc: ok
        print(val)

def test_walrus_in_branch() -> None:
    x: int32 = 10
    if x > 5:
        y = (n := x + 1) * 2  # tpyc: ok
        print(n, y)
    print("done")

def double(x: int32) -> int32:
    return x * 2

def test_walrus_elif() -> None:
    x: int32 = 5
    if x > 10:
        print("big")
    elif (v := get_opt(x)) is not None:  # tpyc: ok
        print(v)
    else:
        print("none")

def test_comprehension_walrus() -> None:
    # PEP 572: walrus in comprehension leaks to enclosing scope
    items = [1, 2, 3, 4, 5]
    filtered = [y for x in items if (y := double(x)) > 5]  # tpyc: ok
    print(filtered)
    print(y)

def main() -> None:
    test_if_condition()
    test_optional_narrowing()
    test_and_chain()
    test_while_loop()
    test_expression_position()
    test_multiple_walrus()
    test_reuse_walrus_target()
    test_walrus_in_branch()
    test_walrus_elif()
    test_comprehension_walrus()

main()
