# Error: set literal with mixed element types
def main() -> None:
    s = {1, "hello"}  # tpyc: error(/mixed element types/)

main()
