# **kwargs with Unpack[TypedDict]: basic keyword construction
from typing import TypedDict, Unpack
from tpy import int32

class Options(TypedDict):
    host: str
    port: int32

def connect(**kwargs: Unpack[Options]) -> None:
    print(kwargs["host"])
    print(kwargs["port"])

def main() -> None:
    connect(host="localhost", port=int32(8080))
    connect(host="example.com", port=int32(443))

main()
