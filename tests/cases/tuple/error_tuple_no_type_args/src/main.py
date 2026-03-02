# Error: bare tuple without type arguments
def main() -> None:
    x: tuple = (1, 2)  # tpyc: error(/tuple requires type arguments/)

main()
