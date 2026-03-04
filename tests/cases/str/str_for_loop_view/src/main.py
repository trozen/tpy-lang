# For-loop str variable inferred as string_view when read-only
def main() -> None:
    words: list[str] = ["hello", "world", "foo"]

    # Read-only iteration -> string_view
    for w in words:
        print(w)

    # Dict key iteration -> string_view
    d: dict[str, int] = {"a": 1, "b": 2}
    for k in d:
        print(k)

    # Dict items -> tuple unpack, str key is string_view
    for k, v in d.items():
        print(k, v)

main()
