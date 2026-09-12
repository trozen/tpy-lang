from tpy import int32

def value_type_uninit() -> None:
    x: int32
    print(x)  # tpyc: error(/may not be assigned at this point/)
