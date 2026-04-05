# missing required keyword-only parameter should error
def greet(name: str, *, greeting: str) -> None:
    print(greeting, name)

def main() -> None:
    greet("Alice")  # tpyc: error(/keyword argument/)

main()
