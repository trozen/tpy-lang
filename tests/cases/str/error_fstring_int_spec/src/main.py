# Error: format specs on int (BigInt) are not yet supported.

def main() -> None:
    x: int = 255
    print(f"{x:d}")  # tpyc: error(/Format specs on int/)

main()
