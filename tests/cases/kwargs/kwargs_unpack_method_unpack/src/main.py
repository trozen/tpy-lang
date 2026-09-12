# **kwargs: method call with **expr unpacking
from typing import TypedDict, Unpack
from tpy import int32

class Options(TypedDict):
    host: str
    port: int32

class Client:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def connect(self, **kwargs: Unpack[Options]) -> None:
        print(self.name)
        print(kwargs["host"])
        print(kwargs["port"])

def main() -> None:
    c = Client(name="MyClient")
    opts = Options(host="example.com", port=int32(443))
    c.connect(**opts)

main()
