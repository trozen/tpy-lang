# TypedDict: field default values are ignored (warning emitted), all fields required
from typing import TypedDict
from tpy import int32

class Config(TypedDict):
    host: str = "localhost"  # tpyc: warning(/default value.*ignored/)
    port: int32 = int32(8080)  # tpyc: warning(/default value.*ignored/)
    debug: bool = False  # tpyc: warning(/default value.*ignored/)

def main() -> None:
    # All fields must be provided at direct construction
    c = Config(host="example.com", port=int32(9090), debug=True)
    print(c["host"])
    print(c["port"])
    print(c["debug"])

main()
