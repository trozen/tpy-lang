# Empty dict with no usage that could infer types
def main() -> None:
    d = {}  # tpyc: error(/Cannot infer types for dict/)
    print(d)

main()
