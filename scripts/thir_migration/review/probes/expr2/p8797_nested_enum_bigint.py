# default-int BigInt
from enum import Enum
from tpy import int32
class Message:
    class Kind(Enum):
        TEXT = 1
        IMAGE = 2
def main() -> None:
    n = 1
    k = Message.Kind(n)
    print(k)
main()
