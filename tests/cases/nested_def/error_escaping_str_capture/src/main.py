# Error: escaping closure captures str/Optional[str] param (string_view would dangle)
from typing import Callable, Optional

def make_greeter(name: str) -> Callable[[], None]:
    def greet() -> None:  # tpyc: error(/captures str parameter.*would dangle/)
        print(name)
    return greet

def make_optional_greeter(name: Optional[str]) -> Callable[[], None]:
    def greet() -> None:  # tpyc: error(/captures str parameter.*would dangle/)
        if name is not None:
            print(name)
    return greet
