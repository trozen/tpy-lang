# Error: qualified dataclasses.field() used outside @dataclass
import dataclasses
from tpy import Int32

class NotDataclass:
    value: Int32 = dataclasses.field(default=0)  # tpyc: error(/can only be used in classes decorated with a macro/)

def main() -> None:
    pass

main()
