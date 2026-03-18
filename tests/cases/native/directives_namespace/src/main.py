# Test that # tpy: namespace overrides the default tpyapp namespace
# tpy: cpp_namespace("myproject::core")

def greet(name: str) -> str:
    return "hello " + name

def main() -> None:
    print(greet("world"))

main()
