/**
 * `::tpy::__bool__` over the builtins that define `__bool__`, the one form
 * `bool(x)` renders for every argument.
 *
 * Pins: each builtin's truth value (zero / empty is false, NaN and -0.0 as
 * Python has them); a `list[bool]` element (a bit proxy, mutable or const)
 * reads its bit; no implicit conversion makes a pointer, `std::optional` or
 * `char` callable, so the C++ `Truthy` concept admits only what the stubs do
 * and a `char`'s truth test stays its `__len__`; a class deriving a builtin
 * inherits its test unless it defines its own `__bool__` (a `StrView` one
 * too), and a str subclass's `__len__` decides at its own width.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "tpy/tpy.hpp"

namespace {

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

template<typename T>
concept free_bool = requires(const T& t) { ::tpy::__bool__(t); };

// Converts to BigInt but is no int: its truth test is its `__len__`.
struct ToBigInt {
    operator ::tpy::BigInt() const { return ::tpy::BigInt(0); }
    int32_t __len__() const { return 1; }
};
// A class deriving a builtin inherits its truth test...
struct SubStr : std::string { using std::string::string; };
struct SubInt : ::tpy::BigInt { using ::tpy::BigInt::BigInt; };
// ...unless it defines its own.
struct OwnStr : std::string {
    bool __bool__() const { return true; }
};
// A mutating override is not skipped for the inherited test.
struct MutStr : std::string {
    bool __bool__() { return true; }
};
// An own `__bool__` of a non-bool result, or an overload set, still overrides.
struct TextBoolStr : std::string {
    std::string __bool__() const { return "yes"; }
};
struct OverloadedBoolInt : ::tpy::BigInt {
    bool __bool__() const { return true; }
    bool __bool__() { return false; }
};
// The inherited test reads the base, not a subclass's own `!=` or `empty`.
struct NeInt : ::tpy::BigInt {
    using ::tpy::BigInt::BigInt;
    // Members, not hidden friends: unused friends warn under clang's -Wunused-function.
    bool operator!=(int) const { return false; }
    bool operator==(int) const { return true; }
};
struct EmptyStr : std::string {
    using std::string::string;
    bool empty() const { return false; }
};
// A str's truth is its length, so an overriding `__len__` decides.
struct LenStr : std::string {
    int32_t __len__() const { return 1; }
};
// A length wider than `int32_t` is not narrowed into a false test.
struct WideLenStr : std::string {
    int64_t __len__() const { return int64_t{1} << 32; }
};
struct ZeroLenStr : std::string {
    using std::string::string;
    int64_t __len__() const { return 0; }
};
// A `StrView` subclass tests its text, as a `str` one does.
struct SubView : std::string_view { using std::string_view::string_view; };

}  // namespace

static_assert(!free_bool<ToBigInt>, "a conversion to BigInt is not an int");
static_assert(free_bool<SubStr> && free_bool<SubInt>, "a builtin's subclass inherits __bool__");
static_assert(free_bool<SubView>, "a StrView subclass inherits __bool__");
static_assert(!free_bool<MutStr>, "a mutating __bool__ override is not replaced by the inherited test");
static_assert(!free_bool<TextBoolStr>, "an own __bool__ of any result type is not replaced by the inherited test");
static_assert(::tpy::dunder_detail::member_bool_any<OverloadedBoolInt>, "an overloaded own __bool__ is detected");

static_assert(::tpy::__bool__(int8_t{-1}) && !::tpy::__bool__(uint64_t{0}));
static_assert(!::tpy::__bool__(0.0f) && ::tpy::__bool__(true));
static_assert(!free_bool<const int*>, "a pointer is not Truthy");
static_assert(!free_bool<std::optional<int32_t>>, "an optional is not Truthy");
static_assert(!free_bool<char>, "a char has no __bool__");
static_assert(!free_bool<const char*>, "a C string is not a str");

int main() {
    check(!::tpy::__bool__(int32_t{0}) && ::tpy::__bool__(int64_t{-3}), "fixed ints");
    check(::tpy::__bool__(uint8_t{255}) && !::tpy::__bool__(uint16_t{0}), "unsigned ints");
    check(!::tpy::__bool__(-0.0) && ::tpy::__bool__(std::nan("")), "-0.0 is false, nan is true");
    check(!::tpy::__bool__(::tpy::BigInt(0)) && ::tpy::__bool__(::tpy::BigInt(-7)), "BigInt");
    check(!::tpy::__bool__(std::string()) && ::tpy::__bool__(std::string("a")), "std::string");
    check(!::tpy::__bool__(::tpy::String()) && ::tpy::__bool__(::tpy::String("a")), "String");
    check(!::tpy::__bool__(std::string_view()) && ::tpy::__bool__(std::string_view("a")), "string_view");
    check(!::tpy::__bool__("") && ::tpy::__bool__("a"), "string literal");
    {
        std::vector<bool> xs{true, false};
        const std::vector<bool>& cx = xs;
        check(::tpy::__bool__(xs[0]) && !::tpy::__bool__(xs[1]), "mutable bit proxy");
        check(::tpy::__bool__(cx[0]) && !::tpy::__bool__(cx[1]), "const bit proxy");
    }
    check(::tpy::truthy('\0'), "a char is a one-character str: always true");
    check(::tpy::truthy(ToBigInt{}), "a BigInt-converting class tests its __len__");
    check(!::tpy::__bool__(SubStr()) && ::tpy::__bool__(SubStr("a")), "str subclass");
    check(!::tpy::__bool__(SubInt(0)) && ::tpy::__bool__(SubInt(2)), "int subclass");
    check(::tpy::__bool__(OwnStr()), "a subclass's own __bool__ wins");
    check(::tpy::__bool__(NeInt(3)) && !::tpy::__bool__(NeInt(0)), "int subclass with its own != / ==");
    check(!::tpy::__bool__(EmptyStr()) && ::tpy::__bool__(EmptyStr("x")), "str subclass with its own empty()");
    check(::tpy::__bool__(LenStr()) && ::tpy::truthy(LenStr()), "str subclass overriding __len__");
    check(::tpy::__bool__(SubStr("x")), "non-empty str subclass");
    check(::tpy::__bool__(WideLenStr()), "a __len__ past int32 is not narrowed to false");
    check(!::tpy::__bool__(ZeroLenStr("x")), "a zero __len__ override is false over non-empty text");
    check(!::tpy::__bool__(SubView()) && ::tpy::__bool__(SubView("a")), "StrView subclass");
    return failures == 0 ? 0 : 1;
}
