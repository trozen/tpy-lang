from tpy import int32


def is_missing(x: int32 | None) -> bool:
    return x == None  # tpyc: error(/Use 'is None' \/ 'is not None'/)
