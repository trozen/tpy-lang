# The C-ABI gate checks the RETURN type, not just the params: returning a str
# from an extern "C" function would emit a std::string return no C caller can
# spell.
from tpy.extern import export

@export(binding="C")
def name_of() -> str:  # tpyc: error(/return type 'str' is not representable in the C ABI; use Ptr\[readonly\[uint8\]\] and convert with tpy.unsafe.unsafe_str_from_cstr\(\)/)
    return "tpy"
