# Error: set() constructor without type annotation
def main() -> None:
    s = set()  # tpyc: error(/Cannot infer element type/)

main()
