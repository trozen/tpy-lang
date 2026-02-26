# Error: slice step is not yet supported.

def main() -> None:
    s: str = "hello"
    print(s[::2])  # tpyc: error(/step/)

main()
