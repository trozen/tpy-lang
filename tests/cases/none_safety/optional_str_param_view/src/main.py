# Optional[str] params should use std::optional<std::string_view> (zero-copy, like str params)
from typing import Optional

def pass_through(s: Optional[str]) -> Optional[str]:
    return s

def assign_local(s: Optional[str]) -> None:
    local: Optional[str] = s
    if local is not None:
        print(local)
    else:
        print("none")

def unwrap(s: Optional[str]) -> str:
    if s is not None:
        return s
    return "default"

def append_to_list(items: list[Optional[str]], s: Optional[str]) -> None:
    items.append(s)

def normalize(s: Optional[str]) -> Optional[str]:
    if s is None:
        s = "default"
    return s

def main() -> None:
    print(pass_through("hello"))
    print(pass_through(None))
    assign_local("world")
    assign_local(None)
    print(unwrap("value"))
    print(unwrap(None))

    items: list[Optional[str]] = []
    append_to_list(items, "a")
    append_to_list(items, None)
    append_to_list(items, "b")
    print(len(items))

    print(normalize("hello"))
    print(normalize(None))

main()
