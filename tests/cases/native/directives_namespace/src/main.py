# Test that # tpy: namespace overrides the default tpy_user namespace
# tpy: cpp_namespace("myproject::core")

def greet(name: str) -> str:
    return "hello " + name

def main() -> None:
    print(greet("world"))

main()
