# Test error: f-string !r conversion on a type without __repr__ method

def main() -> None:
    x: int = 42
    s: str = f"{x!r}"  # tpyc: error(/!r conversion/)
    print(s)

main()
