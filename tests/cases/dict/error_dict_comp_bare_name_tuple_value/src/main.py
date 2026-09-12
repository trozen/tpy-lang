# A dict comprehension whose VALUE expression is a bare name of tuple type: the
# value leg would need the whole tuple copied into storage, which it has no arm
# for. The dict comprehension still rejects.
from tpy import int32


def f(src: dict[str, tuple[int32, int32]]) -> None:
    d = {k: v for k, v in src.items()}  # tpyc: error(/dict_comp/)
    print(len(d))


def main() -> None:
    f({"a": (1, 2)})


main()
