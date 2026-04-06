# TypedDict with default values on fields
from typing import TypedDict
from tpy import Int32

class Config(TypedDict):
    host: str = "localhost"
    port: Int32 = Int32(8080)
    debug: bool = False

def main() -> None:
    # Use all defaults
    c1 = Config()
    print(c1["host"])
    print(c1["port"])
    print(c1["debug"])

    # Override some fields
    c2 = Config(port=Int32(9090), debug=True)
    print(c2["host"])
    print(c2["port"])
    print(c2["debug"])

main()
