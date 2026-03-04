# hash() on Enum and IntEnum types
from enum import Enum, IntEnum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

class Priority(IntEnum):
    Low = 1
    High = 2

def main() -> None:
    # Same member produces same hash
    print(hash(Color.Red) == hash(Color.Red))
    print(hash(Priority.Low) == hash(Priority.Low))

    # Different members produce different hashes
    print(hash(Color.Red) != hash(Color.Green))
    print(hash(Priority.Low) != hash(Priority.High))
    print("ok")

main()
