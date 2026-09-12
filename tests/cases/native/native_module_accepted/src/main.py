# Verify that @native, @native(binding="C"), @native class, and @cpp_template
# in a native_module produce code that compiles, links, and runs correctly.
from tpy import int32
from nativelib import native_func, native_c_func, NativeClass, tmpl_add

def main() -> None:
    print(native_func(int32(10)))
    print(native_c_func(int32(5)))
    print(tmpl_add(int32(3), int32(4)))
    c = NativeClass(int32(42))
    print(c.value)

main()
