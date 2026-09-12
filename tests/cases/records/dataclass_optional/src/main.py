# @dataclass with Optional fields and None defaults
from dataclasses import dataclass
from typing import Optional
from tpy import int32

@dataclass
class Node:
    value: int32
    label: Optional[str] = None

def main() -> None:
    n1 = Node(42)
    print(n1)
    n2 = Node(10, "hello")
    print(n2)

main()
