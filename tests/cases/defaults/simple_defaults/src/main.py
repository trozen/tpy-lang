# Default parameter values for free functions: int, str, bool, float
from tpy import int32

def greet(name: str, greeting: str = "Hello") -> None:
    print(f"{greeting}, {name}!")

def add(a: int32, b: int32 = int32(0)) -> int32:
    return a + b

def scale(value: float, factor: float = 1.0) -> float:
    return value * factor

def log(msg: str, verbose: bool = False) -> None:
    if verbose:
        print(f"[V] {msg}")
    else:
        print(msg)

def main() -> None:
    greet("World")
    greet("World", "Hi")

    print(add(int32(5)))
    print(add(int32(5), int32(3)))

    print(scale(2.5))
    print(scale(2.5, 3.0))

    log("info")
    log("debug", True)

main()
