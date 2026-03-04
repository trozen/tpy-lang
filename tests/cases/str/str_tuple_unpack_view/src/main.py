# Tuple unpack str targets inferred as string_view when read-only
def get_pair() -> tuple[str, str]:
    return ("hello", "world")

def main() -> None:
    # Direct unpack, read-only -> string_view
    a, b = get_pair()
    print(a)
    print(b)

    # Nested in loop with dict items
    d: dict[str, str] = {"key1": "val1", "key2": "val2"}
    for k, v in d.items():
        print(k, v)

main()
