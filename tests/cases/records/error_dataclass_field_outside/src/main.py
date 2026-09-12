# Error: field() used outside @dataclass
from dataclasses import field
from tpy import int32

class NotDataclass:
    value: int32 = field(default=0)  # tpyc: error(/can only be used in classes decorated with a macro/)

def main() -> None:
    pass

main()
