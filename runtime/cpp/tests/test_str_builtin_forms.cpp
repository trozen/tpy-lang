/**
 * `::tpy::__str__` over the builtin scalars and strings, the one form
 * `str(x)` renders for every argument.
 *
 * Pins: a temporary str or String hands its buffer to the result (no copy);
 * an lvalue is copied once; a `list[bool]` element (a bit proxy, mutable or
 * const) renders True/False, not 1/0, under str and repr; the 8-bit
 * integers render as numbers; a class deriving `str`, `String`, `StrView` or
 * `int` renders its base text (a str subclass's own `__repr__` is not its
 * text, an int subclass's is); a `__str__` returning a `char` renders as a
 * one-character str.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <concepts>
#include <cstdint>
#include <cstdio>
#include <string>
#include <string_view>
#include <utility>
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

// Longer than any SSO buffer, so the data pointer tells a move from a copy.
const char* const kLong = "a string long enough to live on the heap, past every SSO buffer";

template<typename T>
concept free_str = requires(const T& t) { { ::tpy::__str__(t) } -> std::convertible_to<std::string_view>; };

struct SubStr : std::string { using std::string::string; };
struct SubString : ::tpy::String { using ::tpy::String::String; };
struct SubView : std::string_view { using std::string_view::string_view; };
struct SubInt : ::tpy::BigInt { using ::tpy::BigInt::BigInt; };
struct TextRepr : std::string {
    using std::string::string;
    std::string __repr__() const { return "repr"; }
};
struct NumRepr : ::tpy::BigInt {
    using ::tpy::BigInt::BigInt;
    std::string __repr__() const { return "repr"; }
};
struct CharStr {
    char __str__() const { return 'x'; }
};

}  // namespace

static_assert(free_str<SubStr> && free_str<SubString> && free_str<SubView> && free_str<SubInt>,
              "a builtin's subclass has the builtin's text");
static_assert(free_str<TextRepr> && free_str<NumRepr>, "a subclass with its own __repr__ has text");
static_assert(free_str<CharStr>, "a char __str__ result is text");

int main() {
    {
        std::string s(kLong);
        const char* buf = s.data();
        std::string r = ::tpy::__str__(std::move(s));
        check(r.data() == buf, "a temporary std::string moves into the result");
    }
    {
        ::tpy::String s(kLong);
        const char* buf = s.data();
        std::string r = ::tpy::__str__(std::move(s));
        check(r.data() == buf, "a temporary String moves into the result");
    }
    {
        std::string s(kLong);
        std::string r = ::tpy::__str__(s);
        check(r == s && r.data() != s.data(), "an lvalue std::string is copied, not moved from");
        ::tpy::String t(kLong);
        std::string u = ::tpy::__str__(t);
        check(u == t && t == kLong, "an lvalue String is copied, not moved from");
    }
    {
        std::vector<bool> xs{true, false};
        const std::vector<bool>& cx = xs;
        check(::tpy::__str__(xs[0]) == "True", "mutable bit proxy, true");
        check(::tpy::__str__(xs[1]) == "False", "mutable bit proxy, false");
        check(::tpy::__str__(cx[0]) == "True", "const bit proxy, true");
        check(::tpy::__str__(cx[1]) == "False", "const bit proxy, false");
        check(::tpy::repr_of(xs[0]) == "True", "repr of a mutable bit proxy");
        check(::tpy::repr_of(cx[1]) == "False", "repr of a const bit proxy");
    }
    check(::tpy::__str__(static_cast<int8_t>(-5)) == "-5", "int8 renders as a number");
    check(::tpy::__str__(static_cast<uint8_t>(255)) == "255", "uint8 renders as a number");
    check(::tpy::__str__('c') == "c", "char renders as its character");
    check(::tpy::__str__(SubStr("s")) == "s", "str subclass");
    check(::tpy::__str__(SubString("t")) == "t", "String subclass");
    check(::tpy::__str__(SubView("v")) == "v", "StrView subclass");
    check(::tpy::__str__(SubInt(7)) == "7", "int subclass");
    check(::tpy::__str__(TextRepr("text")) == "text", "a str subclass's __repr__ is not its text");
    check(::tpy::__str__(NumRepr(7)) == "repr", "an int subclass's __repr__ is its text");
    check(::tpy::__str__(CharStr()) == "x", "a char __str__ result");
    return failures == 0 ? 0 : 1;
}
