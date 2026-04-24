# BytesView return referencing a local owned bytearray dangles after the
# function returns. Previously missed: check_dangling_reference had an early
# exit for all value types, which accepted BytesView returns borrowing from
# locals.
from tpy import BytesView

def bad_bytesview_local_bytearray() -> BytesView:
    ba = bytearray(b"hi")
    return ba  # tpyc: error(/Cannot return BytesView referencing a local or temporary/)
