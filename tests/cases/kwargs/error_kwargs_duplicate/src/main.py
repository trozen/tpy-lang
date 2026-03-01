# Error: same parameter by position and keyword

def add(a: int, b: int) -> int:
    return a + b

def main() -> None:
    print(add(1, 2, b=3))  # tpyc: error(/multiple values for argument 'b'/)

main()
