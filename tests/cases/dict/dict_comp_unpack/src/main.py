# Dict comprehension: tuple unpacking in generator
from tpy import int32

def main() -> None:
    # Unpack from dict.items()
    prices: dict[str, int32] = {"apple": 3, "banana": 1, "cherry": 5}
    doubled: dict[str, int32] = {k: v * 2 for k, v in prices.items()}
    for k in doubled:
        print(k, doubled[k])

    # Unpack from list of tuples
    pairs: list[tuple[str, int32]] = [("x", 10), ("y", 20), ("z", 30)]
    result: dict[str, int32] = {k: v for k, v in pairs}
    print(result["x"])
    print(result["y"])
    print(result["z"])

    # Swap keys and values (int32 -> str)
    swapped: dict[int32, str] = {v: k for k, v in prices.items()}
    print(swapped[3])
    print(swapped[1])
    print(swapped[5])

main()
