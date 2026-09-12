# Returning a dict literal without Own[] is a dangling reference error
from tpy import int32

def make_dict() -> dict[str, int32]:
    return {"x": 1, "y": 2}  # tpyc: error(/Cannot return local or temporary as reference/)

def main() -> None:
    d = make_dict()
    print(d)
main()
