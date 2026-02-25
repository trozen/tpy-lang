# Optional[str] truthiness and None checks
from typing import Optional

def check_truthy(s: Optional[str]) -> None:
    if s:
        print(s)
    else:
        print("falsy")

def check_none(s: Optional[str]) -> None:
    if s is not None:
        print(s)
    else:
        print("none")

def main() -> None:
    check_truthy("hello")
    check_truthy("")
    check_truthy(None)
    check_none("world")
    check_none(None)

main()
