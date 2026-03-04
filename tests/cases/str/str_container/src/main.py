# str(), repr(), and f-string formatting for containers (tuple, list, dict)
def main() -> None:
    # Tuple
    t: tuple[int, str] = (1, "hello")
    print(str(t))
    print(repr(t))
    print(f"{t}")
    print(f"{t!s}")
    print(f"{t!r}")

    # Single-element tuple
    t1: tuple[int] = (42,)
    print(str(t1))

    # List
    xs: list[int] = [1, 2, 3]
    print(str(xs))
    print(repr(xs))
    print(f"{xs}")

    # Dict
    d: dict[str, int] = {"a": 1, "b": 2}
    print(str(d))
    print(repr(d))
    print(f"{d}")

    # Nested containers
    nested: list[tuple[int, str]] = [(1, "a"), (2, "b")]
    print(str(nested))

    # Container in f-string with other parts
    nums: list[int] = [10, 20]
    print(f"nums={nums}")

main()
