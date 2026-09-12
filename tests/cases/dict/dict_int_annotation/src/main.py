# dict literal with `int` (BigInt) annotation -- literal ints resolve to BigInt, not int32
def main() -> None:
    d: dict[str, int] = {"a": 1, "b": 2}
    print(d["a"])
    print(d)
main()
