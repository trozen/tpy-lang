# Error: '%' percentage type in f-string format spec is not supported.

def main() -> None:
    x: float = 0.85
    print(f"{x:%}")  # tpyc: error(/percentage.*is not supported/)

main()
