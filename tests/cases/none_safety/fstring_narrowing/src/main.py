# Optional narrowing propagates into f-string codegen: after
# proving a value is present, f-string interpolation uses the
# unwrapped type instead of std::optional<T>.
from typing import Optional
from tpy import int32

def greet(name: Optional[str]) -> None:
    if name is None:
        return
    print(f"hello, {name}")

def show_int(x: Optional[int32]) -> None:
    if x is None:
        return
    print(f"value is {x}")

def show_if_else(x: Optional[str]) -> None:
    if x is not None:
        print(f"got: {x}")
    else:
        print("nothing")

def main() -> None:
    greet("world")
    greet(None)
    show_int(int32(42))
    show_int(None)
    show_if_else("test")
    show_if_else(None)

main()
