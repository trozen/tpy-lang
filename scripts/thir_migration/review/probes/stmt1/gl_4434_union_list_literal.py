from tpy import int32
u: list[int32] | int32 = [1, 2]
if isinstance(u, list):
    print(len(u))
