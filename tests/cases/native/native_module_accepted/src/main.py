# Verify that @native, @native(binding="C"), @native class, and @cpp_template
# in a native_module produce code that compiles, links, and runs correctly.
from tpy import Int32
from nativelib import native_func, native_c_func, NativeClass, tmpl_add

def main() -> None:
    print(native_func(Int32(10)))
    print(native_c_func(Int32(5)))
    print(tmpl_add(Int32(3), Int32(4)))
    c = NativeClass(Int32(42))
    print(c.value)

main()
