# Test escaping closure copying String local used after closure: warns about copy
from typing import Callable
from tpy import String

def make_greeter(name: str) -> Callable[[], None]:
    owned_name = String(name)
    def greet() -> None:  # tpyc: warning(/copies local 'owned_name'.*used after closure/)
        print("Hello, " + owned_name)
    print(owned_name)
    return greet

def main() -> None:
    greeter = make_greeter("world")
    greeter()

main()
