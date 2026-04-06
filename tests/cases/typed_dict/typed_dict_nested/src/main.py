# TypedDict as a field inside another struct, chained access
from typing import TypedDict
from tpy import Int32, copy

class Address(TypedDict):
    city: str
    zip_code: Int32

class Person:
    name: str
    addr: Address
    def __init__(self, name: str, addr: Address) -> None:
        self.name = name
        self.addr = copy(addr)

def main() -> None:
    addr = Address(city="Berlin", zip_code=Int32(10115))
    p = Person(name="Alice", addr=addr)

    # Chained read: struct field then TypedDict subscript
    print(p.addr["city"])
    print(p.addr["zip_code"])

    # Chained write
    p.addr["zip_code"] = Int32(10117)
    print(p.addr["zip_code"])

main()
