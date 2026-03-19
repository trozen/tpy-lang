# Error: field() used outside @dataclass
from dataclasses import field
from tpy import Int32

class NotDataclass:
    value: Int32 = field(default=0)  # tpyc: error(/can only be used in classes decorated with a macro/)

def main() -> None:
    pass

main()
