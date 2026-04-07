# **kwargs forwarding: wrapper(**kw) -> inner(**kw)
from typing import TypedDict, Unpack
from tpy import Int32

class Options(TypedDict):
    host: str
    port: Int32

def connect(**kwargs: Unpack[Options]) -> None:
    print(kwargs["host"])
    print(kwargs["port"])

def wrapper(**kwargs: Unpack[Options]) -> None:
    print("forwarding...")
    connect(**kwargs)

def main() -> None:
    wrapper(host="example.com", port=Int32(443))

main()
