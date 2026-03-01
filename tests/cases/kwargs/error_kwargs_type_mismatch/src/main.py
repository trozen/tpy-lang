# Error: kwarg value has wrong type (non-generic and generic)

def add(a: int, b: int) -> int:
    return a + b

def main() -> None:
    add(a=1, b="hello")  # tpyc: error(/Type mismatch/)

main()
