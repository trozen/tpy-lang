# **kwargs: pass TypedDict instance via **expr unpacking
from typing import TypedDict, Unpack
from tpy import int32

class Options(TypedDict):
    host: str
    port: int32

def connect(**kwargs: Unpack[Options]) -> None:
    print(kwargs["host"])
    print(kwargs["port"])

def main() -> None:
    opts = Options(host="example.com", port=int32(443))
    connect(**opts)

main()
