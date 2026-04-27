# Error: print() sep=/end= must be string-typed.
def main() -> None:
    print("hello", sep=42)  # tpyc: error(/'sep' argument must be a string/)

main()
