# native_field() on a @native(binding="C") struct -- matches the _bindings/ use
# case where a C struct (e.g. struct timeval with tv_sec/tv_usec) is exposed
# under friendlier Python field names.
from tpy.extern import native, native_field
from tpy import Int32, UInt16

@native("sockaddr_like", binding="C")
class SockAddr:
    family: UInt16 = native_field("sin_family")
    port: UInt16 = native_field("sin_port")

def main() -> None:
    # C aggregate init is positional; the rename only affects field access.
    a = SockAddr(UInt16(2), UInt16(443))
    print(a.family)
    print(a.port)
    a.port = UInt16(8080)
    print(a.port)

main()
