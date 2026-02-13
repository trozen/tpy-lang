from tpy import native, Int32

@native("my_ns::exported_func")
def my_func(x: Int32) -> Int32: ...

@native
def global_export(x: Int32) -> Int32: ...
