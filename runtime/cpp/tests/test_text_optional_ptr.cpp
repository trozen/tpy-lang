/**
 * Text rendering of optionals, pointers and unions: `::tpy::__str__` and
 * `::tpy::repr_of`.
 *
 * Pins: an empty optional and a null pointer render None; a pointer renders
 * its pointee, never its address; a union's null pointer alternative is
 * None; an optional or pointer whose payload is a container reaches the
 * container's own overload, declared after the optional / pointer ones
 * (late dispatch); a char pointer is a C string and no `__str__(T*)`
 * candidate; print_optional over a pointer streams through the Formatter it
 * is given, so an 8-bit int prints as a number, and with none (a generic
 * `T`) it still prints a bool, a float or an 8-bit int as Python does.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdint>
#include <cstdio>
#include <optional>
#include <sstream>
#include <string>
#include <type_traits>
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

struct Rec {
    int n;
    std::string __repr__() const { return "Rec(" + std::to_string(n) + ")"; }
};

template <typename T>
std::string streamed(const T& v) {
    std::ostringstream os;
    os << v;
    return os.str();
}

}  // namespace

int main() {
    // Optional.
    std::optional<int32_t> some = 3, none;
    check(::tpy::__str__(some) == "3", "__str__(optional<int>) payload");
    check(::tpy::__str__(none) == "None", "__str__(empty optional)");
    check(::tpy::repr_of(none) == "None", "repr_of(empty optional)");
    std::optional<std::string> s = std::string("hi");
    check(::tpy::__str__(s) == "hi", "__str__(optional<string>) is the text");
    check(::tpy::repr_of(s) == "'hi'", "repr_of(optional<string>) quotes");

    // Pointer: the pointee, never the address.
    Rec r{1};
    Rec* p = &r;
    Rec* null = nullptr;
    const Rec* cp = &r;
    check(::tpy::__str__(p) == "Rec(1)", "__str__(Rec*) renders the pointee");
    check(::tpy::__str__(cp) == "Rec(1)", "__str__(const Rec*) renders the pointee");
    check(::tpy::__str__(null) == "None", "__str__(null Rec*)");
    check(::tpy::repr_of(null) == "None", "repr_of(null Rec*)");
    double d = 3.0;
    check(::tpy::__str__(&d) == "3.0", "__str__(double*) is the float's text");
    check(::tpy::repr_of(&d) == "3.0", "repr_of(double*)");
    int32_t i = 7;
    check(::tpy::__str__(&i) == "7", "__str__(int*) is the number, not the address");

    // A char pointer is a C string, never a one-char pointee.
    check(::tpy::__str__(static_cast<const char*>("ab")) == "ab", "__str__(const char*) is the C string");

    // Late dispatch: a container payload reaches the container overload.
    std::vector<int32_t> xs{1, 2};
    std::optional<std::vector<int32_t>> oxs = xs;
    check(::tpy::__str__(oxs) == "[1, 2]", "__str__(optional<vector>) late dispatch");
    check(::tpy::__str__(&xs) == "[1, 2]", "__str__(vector*) late dispatch");

    // A union's pointer alternative: null renders None.
    std::variant<Rec*, int32_t> va = p, vn = static_cast<Rec*>(nullptr), vi = 5;
    check(::tpy::__str__(va) == "Rec(1)", "__str__(variant) pointer alternative");
    check(::tpy::__str__(vn) == "None", "__str__(variant) null pointer alternative");
    check(::tpy::__str__(vi) == "5", "__str__(variant) value alternative");
    check(::tpy::repr_of(vn) == "None", "repr_of(variant) null pointer alternative");
    std::variant<std::vector<int32_t>, int32_t> vv = xs;
    check(::tpy::__str__(vv) == "[1, 2]", "__str__(variant) container alternative");

    // print_optional over a pointer with a Formatter.
    int8_t b = 5;
    check(streamed(::tpy::print_optional<::tpy::print_int8, int8_t>(&b)) == "5",
          "print_optional<print_int8> prints the number");
    check(streamed(::tpy::print_optional<::tpy::print_float, double>(&d)) == "3.0",
          "print_optional<print_float> prints the float");
    check(streamed(::tpy::print_optional<::tpy::print_float, double>(nullptr)) == "None",
          "print_optional of null");

    // No Formatter (a generic T): the payload still prints as Python does.
    bool flag = true;
    check(streamed(::tpy::print_optional(&d)) == "3.0", "print_optional(double*) as a float");
    check(streamed(::tpy::print_optional(&flag)) == "True", "print_optional(bool*) as True");
    check(streamed(::tpy::print_optional(&b)) == "5", "print_optional(int8_t*) as a number");
    std::optional<bool> oflag = false;
    std::optional<int8_t> ob = static_cast<int8_t>(9), onone;
    check(streamed(::tpy::print_optional_val(oflag)) == "False", "print_optional_val(optional<bool>)");
    check(streamed(::tpy::print_optional_val(ob)) == "9", "print_optional_val(optional<int8_t>)");
    check(streamed(::tpy::print_optional(ob)) == "9", "print_optional over a value optional");
    check(streamed(::tpy::print_optional(onone)) == "None", "print_optional over an empty optional");

    if (failures == 0) std::printf("ok\n");
    return failures == 0 ? 0 : 1;
}
