# `assert` failure throws AssertionError -- catchable in user code.
# Exercises the assert codegen path (not just `raise AssertionError`).


def positive(n: int) -> int:
    assert n > 0, "must be positive"
    return n


def main() -> None:
    print(positive(5))
    try:
        print(positive(-3))
    except AssertionError as e:
        print("caught:", str(e))
    try:
        x = 0
        assert x, "x is falsy"
    except AssertionError as e:
        print("caught:", str(e))


main()
