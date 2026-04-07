# **kwargs: mixed positional params + Unpack[TypedDict] kwargs
from typing import TypedDict, Unpack
from tpy import Int32

class Options(TypedDict):
    port: Int32
    debug: bool

def connect(host: str, **kwargs: Unpack[Options]) -> None:
    print(host)
    print(kwargs["port"])
    print(kwargs["debug"])

def main() -> None:
    # host positional, kwargs explicit
    connect("localhost", port=Int32(9090), debug=False)
    # host as keyword alongside kwargs
    connect(host="example.com", port=Int32(443), debug=True)

main()
