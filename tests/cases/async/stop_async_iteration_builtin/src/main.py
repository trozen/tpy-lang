# StopAsyncIteration is exposed as a builtin exception type next to
# StopIteration. Verifies it round-trips through `raise` / `except`
# outside of any async context (the loop itself is M6's main consumer).


def caught() -> str:
    try:
        raise StopAsyncIteration("done")
    except StopAsyncIteration as e:
        return str(e)


def main() -> None:
    print(caught())


main()
