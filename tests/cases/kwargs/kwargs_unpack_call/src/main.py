# **kwargs: pass TypedDict instance via **expr unpacking
from typing import TypedDict, Unpack
from tpy import Int32

class Options(TypedDict):
    host: str
    port: Int32

def connect(**kwargs: Unpack[Options]) -> None:
    print(kwargs["host"])
    print(kwargs["port"])

def main() -> None:
    opts = Options(host="example.com", port=Int32(443))
    connect(**opts)

main()
