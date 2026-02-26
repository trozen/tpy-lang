# Error: f-string !r conversion is not supported.

def main() -> None:
    x: int = 42
    s: str = f"{x!r}"  # tpyc: error(/!r conversion/)
    print(s)

main()
