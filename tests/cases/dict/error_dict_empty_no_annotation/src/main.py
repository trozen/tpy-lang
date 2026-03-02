# Empty dict literal without type annotation should error
def main() -> None:
    d = {}  # tpyc: error(/Empty dict literal/)

main()
