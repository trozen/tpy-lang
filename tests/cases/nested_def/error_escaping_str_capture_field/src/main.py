# Test escaping closure stored in field that captures str param (must reject)
from typing import Callable

class Greeter:
    greet: Callable[[str], str]

    def __init__(self, prefix: str) -> None:
        def make_greeting(name: str) -> str:  # tpyc: error(/Escaping closure.*captures str parameter/)
            return prefix + " " + name
        self.greet = make_greeting
