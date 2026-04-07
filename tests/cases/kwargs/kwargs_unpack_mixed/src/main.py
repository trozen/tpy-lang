# **kwargs: mixed positional params + Unpack[TypedDict] kwargs with defaults
from typing import TypedDict, Unpack
from tpy import Int32

class Options(TypedDict):
    port: Int32 = Int32(8080)
    debug: bool = False

def connect(host: str, **kwargs: Unpack[Options]) -> None:
    print(host)
    print(kwargs["port"])
    print(kwargs["debug"])

def main() -> None:
    # host positional, override one kwarg default
    connect("localhost", port=Int32(9090))
    # host as keyword alongside kwargs
    connect(host="example.com", port=Int32(443), debug=True)
    # all kwargs defaults, only host provided
    connect("127.0.0.1")

main()
