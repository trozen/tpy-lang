# **kwargs with total=False TypedDict: optional keyword arguments
from typing import TypedDict, Unpack
from tpy import Int32

class Config(TypedDict, total=False):
    host: str
    port: Int32

def start(**kwargs: Unpack[Config]) -> None:
    print(kwargs["host"])
    print(kwargs["port"])

def start_safe(**kwargs: Unpack[Config]) -> None:
    print("started")

def main() -> None:
    # All fields provided
    start(host="localhost", port=Int32(8080))
    # Partial -- only host (port access would panic)
    start_safe(host="example.com")
    # Zero kwargs
    start_safe()

main()
