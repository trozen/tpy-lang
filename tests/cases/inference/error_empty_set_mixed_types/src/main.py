# Incompatible element types in empty set inference
def main() -> None:
    s = set()
    s.add(1)
    s.add("hello")  # tpyc: error(/Type mismatch/)

main()
