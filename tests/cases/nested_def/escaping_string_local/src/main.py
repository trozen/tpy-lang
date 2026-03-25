# Test escaping closure with String local: moved at last use (no extra copy).
# Workaround for str param capture: convert to owned String, capture that.
from typing import Callable
from tpy import String

def make_greeter(name: str) -> Callable[[], None]:
    owned_name = String(name)
    def greet() -> None:  # tpyc: ok
        print("Hello, " + owned_name)
    return greet

def main() -> None:
    greeter = make_greeter("world")
    greeter()

main()
