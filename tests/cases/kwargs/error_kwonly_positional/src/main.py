# passing keyword-only parameter positionally should error
def greet(name: str, *, greeting: str = "Hello") -> None:
    print(greeting, name)

def main() -> None:
    greet("Alice", "Hi")  # tpyc: error(/positional/)

main()
