# Runtime TypeError -- raised by `ord()` on a string whose length is not 1,
# matching CPython's `ord() expected a character, but string of length N found`.
# (`char(s)` raises the same way but is TPy-only and lives in panic_char_from_str.)


def main() -> None:
    try:
        print(ord("ab"))
    except TypeError as e:
        print("caught:", str(e))

    try:
        print(ord(""))
    except TypeError as e:
        print("caught:", str(e))


main()
