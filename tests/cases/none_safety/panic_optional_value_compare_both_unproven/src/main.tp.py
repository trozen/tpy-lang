from tpy import Int32, Bool


def gt_pair(a: Int32 | None, b: Int32 | None) -> Bool:
    return a > b  # tpyc: warning(/Potential None access/)


print(gt_pair(3, 1))
print(gt_pair(None, 1))
