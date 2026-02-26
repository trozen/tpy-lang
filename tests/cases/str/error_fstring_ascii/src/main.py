# Error: f-string !a conversion is not supported.

def main() -> None:
    x: int = 42
    print(f"{x!a}")  # tpyc: error(/!a conversion/)

main()
