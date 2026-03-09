# Test error: dict() with no arguments and no type annotation
def main() -> None:
    d = dict()  # tpyc: error(/Cannot infer types for dict/)

main()
