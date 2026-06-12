# LookupError is the base class of both IndexError and KeyError, matching
# CPython's hierarchy. A single `except LookupError` block catches either
# subtype. The KeyError leg prints a fixed marker because TPy's dict-miss
# message diverges from CPython's repr-of-key format (see key_error_caught).


def main() -> None:
    xs: list[int] = [10, 20, 30]
    try:
        print(xs[5])
    except LookupError as e:
        print("caught lookup:", str(e))

    d: dict[str, int] = {"a": 1}
    try:
        print(d["missing"])
    except LookupError:
        print("caught lookup: dict miss")

    try:
        raise LookupError("by hand")
    except Exception as e:
        print("caught exception:", str(e))

    # Full two-hop chain: IndexError -> LookupError -> Exception.
    try:
        print(xs[9])
    except Exception as e:
        print("index via Exception:", str(e))

    try:
        print(d["gone"])
    except Exception:
        print("key via Exception")


main()
