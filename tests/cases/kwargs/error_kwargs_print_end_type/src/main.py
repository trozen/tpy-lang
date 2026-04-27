# Error: print() end= must be string-typed (parallel to sep=).
def main() -> None:
    print("hello", end=42)  # tpyc: error(/'end' argument must be a string/)

main()
