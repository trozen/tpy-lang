# **kwargs without Unpack annotation should error
def connect(**kwargs) -> None:  # tpyc: error(/Unpack\[TypedDict\] annotation/)
    pass
