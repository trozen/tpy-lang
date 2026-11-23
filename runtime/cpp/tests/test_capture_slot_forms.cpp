/**
 * Escaping-closure capture slot self-check.
 *
 * A closure capturing a `T` parameter holds `capture_slot<storage_t<T>, P>`,
 * `P` the parameter's declared C++ type. Which member that is decides whether
 * the closure aliases the caller's object (a Python closure's view) or keeps
 * an object of its own, and a wrong answer is a silent copy or a dangling
 * reference no program output shows, so it is pinned here:
 *
 *   1. A parameter passed as `X&&` (`T = Own[X]`) owns its value: the slot
 *      holds an `X` member, moved in from the argument.
 *   2. A parameter passed as `X&` / `const X&` (a reference `T`) keeps the
 *      reference, with its const-ness: the closure sees the caller's object.
 *   3. A value type is held by value whatever the parameter's form.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdio>
#include <type_traits>
#include <utility>

#include "tpy/tpy.hpp"

namespace {

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

struct Obj {
    int n = 0;
    int copies = 0;
    Obj() = default;
    explicit Obj(int v) : n(v) {}
    Obj(const Obj& o) : n(o.n), copies(o.copies + 1) {}
    Obj(Obj&& o) noexcept : n(o.n), copies(o.copies) {}
    Obj& operator=(const Obj&) = default;
    Obj& operator=(Obj&&) = default;
};

template<typename T, typename P>
using member_t = decltype(std::declval<tpy::capture_slot<T, P>&>().v);

void member_types() {
    static_assert(std::is_same_v<member_t<Obj, Obj&&>, Obj>,
                  "an X&& parameter's slot holds an X");
    static_assert(std::is_same_v<member_t<Obj, Obj&>, Obj&>,
                  "an X& parameter's slot keeps the reference");
    static_assert(std::is_same_v<member_t<Obj, const Obj&>, const Obj&>,
                  "a const X& parameter's slot keeps the const reference");
    static_assert(std::is_same_v<member_t<int, const int&>, int>,
                  "a value type's slot holds the value");
    static_assert(std::is_same_v<member_t<int, int>, int>,
                  "a by-value parameter's slot holds the value");
}

void owned_slot_moves() {
    Obj arg(3);
    tpy::capture_slot<Obj, Obj&&> slot(std::move(arg));
    check(slot.v.n == 3 && slot.v.copies == 0,
          "an X&& parameter moves into its slot, copying nothing");
    arg.n = 9;
    check(slot.v.n == 3, "an owned slot is no alias of the argument");
}

void borrowed_slot_aliases() {
    Obj arg(4);
    Obj& ref = arg;
    tpy::capture_slot<Obj, Obj&> slot(ref);
    arg.n = 7;
    check(&slot.v == &arg && slot.v.n == 7,
          "an X& parameter's slot sees the caller's object");
    const Obj& cref = arg;
    tpy::capture_slot<Obj, const Obj&> cslot(cref);
    check(&cslot.v == &arg, "a const X& parameter's slot sees the caller's object");
}

}  // namespace

int main() {
    member_types();
    owned_slot_moves();
    borrowed_slot_aliases();
    if (failures != 0) {
        std::printf("%d capture slot check(s) failed\n", failures);
        return 1;
    }
    std::printf("capture slot forms OK\n");
    return 0;
}
