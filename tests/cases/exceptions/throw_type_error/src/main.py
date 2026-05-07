# Explicit `raise TypeError(...)` is the throw-tier path; user code can
# catch it the same way as runtime-thrown TypeError.


def fail(x: int) -> None:
    raise TypeError("custom: type mismatch")


def main() -> None:
    try:
        fail(42)
    except TypeError as e:
        print("caught:", str(e))


main()
