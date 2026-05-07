# Explicit `raise ZeroDivisionError(...)` works the same as runtime-thrown
# ZeroDivisionError; user code can also subclass it (basic catch shape only).


def safe_div(a: int, b: int) -> int:
    if b == 0:
        raise ZeroDivisionError("custom: cannot divide by zero")
    return a // b


def main() -> None:
    print(safe_div(10, 2))
    try:
        print(safe_div(10, 0))
    except ZeroDivisionError as e:
        print("caught:", str(e))


main()
