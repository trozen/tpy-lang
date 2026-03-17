# Test typing.Sized via qualified access (bare `import typing`)
# Verifies same codegen as `from typing import Sized`
import typing
from tpy import Int32

def count(items: typing.Sized) -> Int32:
    return len(items)

def main():
    data: list[Int32] = [1, 2, 3]
    print(count(data))

main()
