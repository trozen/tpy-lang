# Test truthiness for built-in container types (list, str) in if/not/and/or

def check_list(items: list[int], empty: list[int]) -> None:
    if items:
        print("list truthy")
    if not empty:
        print("list falsy")

def check_str(s: str, e: str) -> None:
    if s:
        print("str truthy")
    if not e:
        print("str falsy")

def check_and_or(items: list[int], empty: list[int]) -> None:
    if items and not empty:
        print("and/or works")

def main() -> None:
    check_list([1, 2, 3], [])
    check_str("hello", "")
    check_and_or([1], [])

main()
