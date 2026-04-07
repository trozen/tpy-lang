# Tuple unpacking in multiple assignment should be rejected
def main() -> None:
    a, b = c = [1, 2]  # tpyc: error(/Tuple unpacking not supported in multiple assignment/)

main()
