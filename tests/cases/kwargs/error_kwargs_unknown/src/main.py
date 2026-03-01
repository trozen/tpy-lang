# Error: unknown keyword argument name

def add(a: int, b: int) -> int:
    return a + b

def main() -> None:
    print(add(1, c=2))  # tpyc: error(/unexpected keyword argument 'c'/)

main()
