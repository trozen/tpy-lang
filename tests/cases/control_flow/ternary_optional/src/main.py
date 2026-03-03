# Ternary expression: Optional narrowing and None branches
from typing import Optional

def safe_len(s: Optional[str]) -> int:
    # Optional narrowing: s is narrowed to str in the then-branch
    return len(s) if s is not None else 0

def value_or_none(flag: bool) -> Optional[str]:
    # None in then-branch -> Optional[str]
    return None if flag else "hello"

def none_or_value(flag: bool) -> Optional[str]:
    # None in else-branch -> Optional[str]
    return "world" if flag else None

def with_default(val: Optional[str]) -> str:
    # Common pattern: provide a default for Optional
    return val if val is not None else "default"

def truthy_narrowing(s: Optional[str]) -> str:
    # Truthy condition narrows Optional[str] to str in then-branch
    return s if s else "empty"

def main() -> None:
    print(safe_len("hello"))
    print(safe_len(None))

    r1 = value_or_none(True)
    print(r1)
    r1 = value_or_none(False)
    print(r1)

    r2 = none_or_value(True)
    print(r2)
    r2 = none_or_value(False)
    print(r2)

    print(with_default("custom"))
    print(with_default(None))

    print(truthy_narrowing("hello"))
    print(truthy_narrowing(None))
    print(truthy_narrowing(""))

    # Truthy narrowing in non-return context (print arg, assignment)
    s: Optional[str] = "world"
    print(s if s else "empty")
    s = None
    print(s if s else "empty")

main()
