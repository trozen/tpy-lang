from tpy import Int32

def loop_value(n: Int32) -> None:
    x: Int32
    for i in range(n):
        x = i
    print(x)  # tpyc: error(/may not be assigned at this point/)
