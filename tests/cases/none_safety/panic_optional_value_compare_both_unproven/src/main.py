from tpy import int32


def gt_pair(a: int32 | None, b: int32 | None) -> bool:
    return a > b  # tpyc: warning(/Potential None access/)


print(gt_pair(3, 1))
print(gt_pair(None, 1))
