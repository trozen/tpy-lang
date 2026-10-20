# **kwargs: pass TypedDict instance via **expr unpacking
from typing import TypedDict, Unpack
from tpy import int32, Own

class Options(TypedDict):
    host: str
    port: int32

def connect(**kwargs: Unpack[Options]) -> None:
    print(kwargs["host"])
    print(kwargs["port"])

def keep(o: Own[Options]) -> Own[Options]:
    return o

def used_after_own() -> None:
    # A `**d` operand is a use of `d`: the earlier Own use is not its last,
    # so it copies (and says so) instead of moving `d` away.
    d = Options(host="kept", port=1)
    k = keep(d)  # tpyc: warning(/copies Options into owned storage/)
    connect(**d)
    k["port"] = 2
    print("after own", k["host"], k["port"])

class Conn:
    def connect(self, **kwargs: Unpack[Options]) -> None:
        print("method", kwargs["host"], kwargs["port"])

def used_after_own_method() -> None:
    # The method form: `c.connect(**d)` is a use of `d` as well.
    d = Options(host="kept", port=1)
    k = keep(d)  # tpyc: warning(/copies Options into owned storage/)
    c = Conn()
    c.connect(**d)
    k["port"] = 3
    print("method after own", k["host"], k["port"])

def main() -> None:
    opts = Options(host="example.com", port=443)
    connect(**opts)
    used_after_own()
    used_after_own_method()

main()
