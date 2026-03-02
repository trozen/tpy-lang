# Unhashable type as dict key should error
def main() -> None:
    d = {[1, 2]: 1}  # tpyc: error(/not hashable/)

main()
