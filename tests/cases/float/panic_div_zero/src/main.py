# Float true division by zero panics (matches Python's ZeroDivisionError)
def main() -> None:
    x: float = 10.0
    y: float = 0.0
    z: float = x / y
    print(z)

main()
