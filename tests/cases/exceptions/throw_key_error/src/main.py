# Explicit `raise KeyError(...)` works the same as runtime-thrown KeyError.


def force_miss(k: str) -> int:
    raise KeyError("custom: " + k)


def main() -> None:
    try:
        print(force_miss("missing"))
    except KeyError as e:
        print("caught:", str(e))


main()
