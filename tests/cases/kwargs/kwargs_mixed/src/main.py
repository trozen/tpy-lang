# Positional + keyword arguments mixed

def greet(name: str, greeting: str = "Hello", punctuation: str = "!") -> None:
    print(f"{greeting}, {name}{punctuation}")

def compute(a: int, b: int, c: int) -> int:
    return a + b + c

def main() -> None:
    greet("World", greeting="Hi")
    greet("Bob", punctuation=".")
    print(compute(1, 2, c=3))

main()
