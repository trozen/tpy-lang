from tpy import Int32

class Inner:
    value: Int32

class Outer:
    inner: Inner

# ERROR: returning reference to field of local object
def bad_local_field() -> Inner:
    local: Outer = Outer()
    return local.inner  # tpyc: error(/Cannot return local or temporary/)
