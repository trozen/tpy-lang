# Test that multiple native modules sharing the same cpp_namespace
# compile without collisions (no generated code for native modules)
from mypkg.math import add
from mypkg.text import greet

def main() -> None:
    print("ok")

main()
