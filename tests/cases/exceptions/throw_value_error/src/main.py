# Explicit `raise ValueError(...)` is the throw-tier path; user code can
# catch it the same way as runtime-thrown ValueError. Subclassing works
# (basic catch shape only).


def parse_positive(s: str) -> int:
    if not s:
        raise ValueError("custom: empty input")
    return int(s)


def main() -> None:
    print(parse_positive("42"))
    try:
        print(parse_positive(""))
    except ValueError as e:
        print("caught:", str(e))


main()
