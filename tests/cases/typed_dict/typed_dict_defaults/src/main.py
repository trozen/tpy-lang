# TypedDict: field default values are ignored (warning emitted), all fields required
from typing import TypedDict
from tpy import Int32

class Config(TypedDict):
    host: str = "localhost"  # tpyc: warning(/default value.*ignored/)
    port: Int32 = Int32(8080)  # tpyc: warning(/default value.*ignored/)
    debug: bool = False  # tpyc: warning(/default value.*ignored/)

def main() -> None:
    # All fields must be provided at direct construction
    c = Config(host="example.com", port=Int32(9090), debug=True)
    print(c["host"])
    print(c["port"])
    print(c["debug"])

main()
