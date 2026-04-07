# **kwargs with Unpack[TypedDict]: fields with default values
from typing import TypedDict, Unpack
from tpy import Int32

class Config(TypedDict):
    host: str = "localhost"
    port: Int32 = Int32(8080)
    debug: bool = False

def start(**kwargs: Unpack[Config]) -> None:
    print(kwargs["host"])
    print(kwargs["port"])
    print(kwargs["debug"])

def main() -> None:
    # All defaults
    start()
    # Override some
    start(port=Int32(9090), debug=True)

main()
