# Test calling methods on narrowed Optional[str] values.

def greet(name: str | None) -> None:
    if name is not None:
        print(name.upper())
        print(name.startswith("A"))
    else:
        print("no name")

def main() -> None:
    greet("alice")
    greet(None)

main()
