# Test that `__tpy__` "cpp_namespace" overrides the default tpyapp namespace
__tpy__ = {"cpp_namespace": "myproject::core"}

def greet(name: str) -> str:
    return "hello " + name

def main() -> None:
    print(greet("world"))

main()
