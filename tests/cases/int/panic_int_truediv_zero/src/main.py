# int true division by zero panics (matches Python's ZeroDivisionError)
def main() -> None:
    x: int = 10
    y: int = 0
    z: float = x / y
    print(z)

main()
