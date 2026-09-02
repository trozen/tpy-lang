from tpy import Int32
u: list[Int32] | Int32 = [1, 2]
if isinstance(u, list):
    print(len(u))
