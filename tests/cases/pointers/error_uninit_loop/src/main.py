from tpy import int32

def loop_value(n: int32) -> None:
    x: int32
    for i in range(n):
        x = i
    print(x)  # tpyc: error(/may not be assigned at this point/)
