# Error: slicing an unsupported type (dict).

def main() -> None:
    d: dict[str, int] = {"a": 1, "b": 2}
    print(d[0:1])  # tpyc: error(/Type mismatch in dict key: expected str, got slice/)

main()
