# **kwargs: .get() and "key" in kwargs for safe access on total=False TypedDict
from typing import TypedDict, Unpack
from tpy import int32

class Config(TypedDict, total=False):
    host: str
    port: int32
    verbose: bool

def connect(**kwargs: Unpack[Config]) -> None:
    # "key" in kwargs
    host = kwargs.get("host", "localhost")
    port = kwargs.get("port", int32(3000))
    if "verbose" in kwargs:
        print("verbose mode")
    print(host)
    print(port)

def show_config(**kwargs: Unpack[Config]) -> None:
    # get without default -> Optional[T]
    h = kwargs.get("host")
    if h is not None:
        print(h)
    else:
        print("no host")
    # not in
    if "port" not in kwargs:
        print("no port")

def main() -> None:
    connect(host="example.com", port=int32(8080), verbose=True)
    connect()
    show_config(host="myhost")
    show_config()

main()
