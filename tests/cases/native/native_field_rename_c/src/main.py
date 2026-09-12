# native_field() on a @native(binding="C") struct -- matches the _bindings/ use
# case where a C struct (e.g. struct timeval with tv_sec/tv_usec) is exposed
# under friendlier Python field names.
from tpy.extern import native, native_field
from tpy import int32, uint16

@native("sockaddr_like", binding="C")
class SockAddr:
    family: uint16 = native_field("sin_family")
    port: uint16 = native_field("sin_port")

def main() -> None:
    # C aggregate init is positional; the rename only affects field access.
    a = SockAddr(uint16(2), uint16(443))
    print(a.family)
    print(a.port)
    a.port = uint16(8080)
    print(a.port)

main()
