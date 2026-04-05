# keyword-only parameters with default values
def greet(name: str, *, greeting: str = "Hello", punctuation: str = "!") -> None:
    print(greeting, name, sep="", end="")
    print(punctuation)

def main() -> None:
    greet("Alice")
    greet("Bob", greeting="Hi")
    greet("Charlie", greeting="Hey", punctuation=".")

main()
