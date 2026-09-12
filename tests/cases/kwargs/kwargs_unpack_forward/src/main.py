# **kwargs forwarding: wrapper(**kw) -> inner(**kw)
from typing import TypedDict, Unpack
from tpy import int32

class Options(TypedDict):
    host: str
    port: int32

def connect(**kwargs: Unpack[Options]) -> None:
    print(kwargs["host"])
    print(kwargs["port"])

def wrapper(**kwargs: Unpack[Options]) -> None:
    print("forwarding...")
    connect(**kwargs)

def main() -> None:
    wrapper(host="example.com", port=int32(443))

main()
