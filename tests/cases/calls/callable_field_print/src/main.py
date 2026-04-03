# Test printing records with Callable fields (operator<< for std::function)
from typing import Callable

class Handler:
    action: Callable[[], None]

    def __init__(self) -> None:
        self.action = lambda: print(0)

    def __repr__(self) -> str:
        return "Handler(action=<function>)"

def main() -> None:
    h = Handler()
    print(h)

main()
