# Error: required parameter not covered by positional or keyword arg

def f(a: int, b: int, c: int) -> int:
    return a + b + c

def main() -> None:
    print(f(1, c=3))  # tpyc: error(/missing required argument: 'b'/)

main()
