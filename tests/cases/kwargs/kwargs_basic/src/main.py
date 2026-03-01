# Keyword arguments for free functions: basic usage

def greet(name: str, greeting: str = "Hello") -> None:
    print(f"{greeting}, {name}!")

def add(a: int, b: int) -> int:
    return a + b

def main() -> None:
    greet(name="World")
    greet(greeting="Hi", name="Alice")
    print(add(a=3, b=4))

main()
