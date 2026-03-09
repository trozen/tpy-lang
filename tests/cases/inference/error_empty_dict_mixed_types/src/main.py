# Incompatible value types in empty dict inference
def main() -> None:
    d = {}
    d["a"] = 1
    d["b"] = "hello"  # tpyc: error(/Type mismatch/)

main()
