# Ternary expression: type mismatch error
def main() -> None:
    x = "hello" if True else 42  # tpyc: error(/Incompatible types in ternary/)

main()
