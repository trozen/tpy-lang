# **kwargs with Unpack[TypedDict]: basic keyword construction
from typing import TypedDict, Unpack
from tpy import Int32

class Options(TypedDict):
    host: str
    port: Int32

def connect(**kwargs: Unpack[Options]) -> None:
    print(kwargs["host"])
    print(kwargs["port"])

def main() -> None:
    connect(host="localhost", port=Int32(8080))
    connect(host="example.com", port=Int32(443))

main()
