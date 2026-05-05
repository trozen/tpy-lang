# `assert` failures throw AssertionError (catchable). Matches CPython.


def check(n: int) -> int:
    assert n > 0, "n must be positive"
    return n * 2


def main() -> None:
    print(check(3))
    try:
        print(check(-1))
    except AssertionError as e:
        print("caught:", str(e))


main()
