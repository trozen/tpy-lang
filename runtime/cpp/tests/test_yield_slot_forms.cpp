/**
 * Generic yield-slot self-check.
 *
 * A generic generator spells its element `::tpy::yield_slot_t<T>`, so ONE
 * wrapper serves both the reference and the value instantiations. The three
 * guarantees that makes load-bearing are invisible from generated code -- a
 * wrong one is a silent extra copy or a const hole, not a diagnostic:
 *
 *   1. `yield_slot_t` is idempotent: a type argument sema already substituted
 *      with the slot form must not get a second wrapper.
 *   2. A value payload is MOVED out of the wrapper on the consuming path
 *      (`unwrap_ref_move`), so `list()` / `collect` / `extend` over a generic
 *      generator allocate exactly what the monomorphic twin allocates.
 *   3. `val_or_ref<const V>` never lends a mutable `V&`, although its value
 *      storage drops the const.
 *   4. The `ReferenceType` concept answers a slot by its referent, so a bound
 *      instantiated at the slot spelling is not refused for the wrapper.
 *   5. At a type argument `Own[X]` (the owned tag `own_arg<X>`) every slot
 *      form is the hand-written `Own[X]` twin's: an owned parameter, a
 *      handed-over result and step, and `X` itself as the storage form; at
 *      any other type argument the parameter form is the twin's too.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdio>
#include <cstdlib>
#include <expected>
#include <new>
#include <string>
#include <type_traits>
#include <vector>

#include "tpy/tpy.hpp"

namespace {

long g_allocs = 0;
bool g_counting = false;

struct Point {
    int x;
};

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

// Long enough to defeat the small-string optimization, so every copy of one
// is an allocation the counter sees.
std::vector<std::string> long_strings() {
    return {"a-long-string-well-past-sso-0",
            "a-long-string-well-past-sso-1",
            "a-long-string-well-past-sso-2"};
}

// The monomorphic spelling: `__next__` hands out a plain `std::string`.
struct MonoGen : tpy::next_iter_mixin<MonoGen, std::string> {
    std::vector<std::string> data = long_strings();
    std::size_t i = 0;

    std::expected<std::string, tpy::StopIteration> __next__() {
        if (i >= data.size()) return tpy::make_unexpected(tpy::StopIteration{});
        return data[i++];
    }
    MonoGen& __iter__() { return *this; }
};

// The generic spelling at the same instantiation.
struct SlotGen : tpy::next_iter_mixin<SlotGen, tpy::yield_slot_t<std::string>> {
    std::vector<std::string> data = long_strings();
    std::size_t i = 0;

    std::expected<tpy::yield_slot_t<std::string>, tpy::StopIteration> __next__() {
        if (i >= data.size()) return tpy::make_unexpected(tpy::StopIteration{});
        return data[i++];
    }
    SlotGen& __iter__() { return *this; }
};

void slot_spelling() {
    static_assert(std::is_same_v<tpy::yield_slot_t<Point>,
                                 tpy::val_or_ref<Point>>,
                  "a bare T takes the slot wrapper");
    static_assert(std::is_same_v<tpy::yield_slot_t<tpy::val_or_ref<Point>>,
                                 tpy::val_or_ref<Point>>,
                  "an already-substituted slot is not wrapped twice");
    static_assert(std::is_same_v<tpy::yield_slot_t<const tpy::val_or_ref<Point>>,
                                 tpy::val_or_ref<const Point>>,
                  "const rides INSIDE the single wrapper");
}

// A `T: ReferenceType` bound may be instantiated at the slot spelling (a
// readonly instance argument arrives as `val_or_ref<const X>`), and the slot
// is itself a value type: the concept answers by the referent.
static_assert(tpy::ReferenceType<Point>);
static_assert(tpy::ReferenceType<const Point>);
static_assert(tpy::ReferenceType<std::vector<int>>);
static_assert(tpy::ReferenceType<tpy::val_or_ref<const Point>>);
static_assert(!tpy::ReferenceType<int>);
static_assert(!tpy::ReferenceType<std::string>);
static_assert(!tpy::ReferenceType<tpy::val_or_ref<int>>);
// ... and ValueType is its mirror through the same slot unwrapping.
static_assert(tpy::ValueType<tpy::val_or_ref<int>>);
static_assert(!tpy::ValueType<tpy::val_or_ref<Point>>);
static_assert(tpy::ValueType<const int>);

void accessor_constness() {
    std::string owned = "x";
    tpy::val_or_ref<std::string> slot(owned);
    static_assert(std::is_same_v<decltype(slot.get()), std::string&>,
                  "a mutable value slot lends its own copy mutably");

    tpy::val_or_ref<const std::string> cslot(owned);
    static_assert(std::is_same_v<decltype(cslot.get()), const std::string&>,
                  "a readonly value slot never lends a mutable reference");

    Point p{1};
    tpy::val_or_ref<Point> ref_slot(p);
    static_assert(std::is_same_v<decltype(ref_slot.get()), Point&>,
                  "a reference slot lends the pointee mutably");
    tpy::val_or_ref<const Point> cref_slot(p);
    static_assert(std::is_same_v<decltype(cref_slot.get()), const Point&>,
                  "a readonly reference slot lends the pointee as const");

    // The consuming unwrap moves a value payload out and leaves a reference
    // payload alone (it points at storage the wrapper does not own).
    static_assert(std::is_same_v<decltype(tpy::unwrap_ref_move(slot)),
                                 std::string&&>,
                  "value payload moves out");
    static_assert(std::is_same_v<decltype(tpy::unwrap_ref_move(ref_slot)),
                                 Point&>,
                  "reference payload stays an lvalue");
}

void collect_allocations() {
    MonoGen mono;
    g_counting = true;
    long before = g_allocs;
    auto mono_out = tpy::collect<std::vector<std::string>>(mono);
    long mono_allocs = g_allocs - before;

    SlotGen slot;
    before = g_allocs;
    auto slot_out = tpy::collect<std::vector<std::string>>(slot);
    long slot_allocs = g_allocs - before;
    g_counting = false;

    check(mono_out.size() == 3 && slot_out.size() == 3,
          "both generators yield three elements");
    check(mono_out == slot_out, "both generators yield the same elements");
    check(slot_allocs == mono_allocs,
          "the generic slot allocates exactly what the monomorphic one does");
    if (slot_allocs != mono_allocs) {
        std::printf("  mono=%ld slot=%ld\n", mono_allocs, slot_allocs);
    }
}

} // namespace

// Counting operator new: only the collect() windows above are measured, so
// unrelated startup allocations cannot move the comparison.
void* operator new(std::size_t n) {
    if (g_counting) ++g_allocs;
    void* p = std::malloc(n);
    if (!p) throw std::bad_alloc();
    return p;
}

void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }

namespace owned_tag {
using ::tpy::own_arg;
static_assert(std::is_same_v<::tpy::param_val_or_ref_t<own_arg<Point>>, Point&&>);
static_assert(std::is_same_v<::tpy::own_param_t<own_arg<std::string>>, std::string>);
static_assert(std::is_same_v<::tpy::param_val_or_cref_t<own_arg<Point>>, Point&&>);
static_assert(std::is_same_v<::tpy::val_or_ref_t<own_arg<Point>>, Point>);
static_assert(std::is_same_v<::tpy::yield_slot_t<own_arg<Point>>, Point>);
static_assert(std::is_same_v<::tpy::storage_t<const own_arg<std::string>>,
                             const std::string>);
static_assert(std::is_same_v<::tpy::storage_t<own_arg<Point>>, Point>);
// The owned arms of the borrow-or-value traits: the twin's `Own[X]`
// tuple element and `Own[X] | None` parameter hold the value.
static_assert(std::is_same_v<::tpy::val_or_ptr_t<own_arg<Point>>, Point>);
static_assert(std::is_same_v<::tpy::val_or_cptr_t<own_arg<Point>>, Point>);
static_assert(std::is_same_v<::tpy::val_or_ptr_t<own_arg<std::string>>,
                             std::string>);
static_assert(std::is_same_v<::tpy::val_or_cptr_t<own_arg<std::string>>,
                             std::string>);
static_assert(std::is_same_v<::tpy::opt_param_t<own_arg<Point>>,
                             std::optional<Point>>);
static_assert(std::is_same_v<::tpy::opt_cparam_t<own_arg<Point>>,
                             std::optional<Point>>);
static_assert(std::is_same_v<::tpy::opt_param_t<own_arg<std::string>>,
                             std::optional<std::string>>);
static_assert(std::is_same_v<::tpy::opt_cparam_t<own_arg<std::string>>,
                             std::optional<std::string>>);
// The value arm: a generic `T` parameter takes the hand-written twin's form
// -- by value at a scalar, a view at `str` / `bytes`, a const reference where
// the twin's is one (`int`, `String`, a tuple) -- and a reference type's `T&`.
static_assert(std::is_same_v<::tpy::param_val_or_ref_t<int32_t>, int32_t>);
static_assert(std::is_same_v<::tpy::param_val_or_ref_t<double>, double>);
static_assert(std::is_same_v<::tpy::param_val_or_ref_t<std::string>,
                             std::string_view>);
static_assert(std::is_same_v<::tpy::param_val_or_ref_t<::tpy::Bytes>,
                             ::tpy::BytesView>);
static_assert(std::is_same_v<::tpy::param_val_or_ref_t<::tpy::BigInt>,
                             const ::tpy::BigInt&>);
static_assert(std::is_same_v<::tpy::param_val_or_ref_t<::tpy::String>,
                             const ::tpy::String&>);
static_assert(std::is_same_v<::tpy::param_val_or_ref_t<std::tuple<int32_t, std::string>>,
                             const std::tuple<int32_t, std::string>&>);
static_assert(std::is_same_v<::tpy::param_val_or_ref_t<Point>, Point&>);
static_assert(std::is_same_v<::tpy::param_val_or_cref_t<int32_t>, int32_t>);
static_assert(std::is_same_v<::tpy::param_val_or_cref_t<Point>, const Point&>);
// The `_last` helpers move exactly a slot that holds a value of its own.
static_assert(::tpy::param_holds_value_v<int32_t>);
static_assert(::tpy::param_holds_value_v<own_arg<Point>>);
static_assert(!::tpy::param_holds_value_v<Point>);
static_assert(!::tpy::param_holds_value_v<::tpy::BigInt>);
static_assert(!::tpy::param_holds_value_v<std::string>);
static_assert(!::tpy::param_holds_value_v<std::tuple<int32_t>>);
// Copyable but not movable: its slot holds a value, yet a move would not bind.
struct Pinned {
    Pinned() = default;
    Pinned(const Pinned&) = default;
    Pinned(Pinned&&) = delete;
};
}  // namespace owned_tag
template<> struct tpy::is_value_type<owned_tag::Pinned> : std::true_type {};
namespace owned_tag {
static_assert(std::is_same_v<::tpy::param_val_or_ref_t<Pinned>, Pinned>);
static_assert(!::tpy::param_holds_value_v<Pinned>);
// A hand-off moves a slot that holds a value; a body last use moves only the
// owned tag (the twin copies `g(x)` of a by-value parameter).
static_assert(std::is_same_v<decltype(::tpy::param_handoff<int32_t>(std::declval<int32_t&>())),
                             int32_t&&>);
static_assert(std::is_same_v<decltype(::tpy::param_handoff<Pinned>(std::declval<Pinned&>())),
                             Pinned&>);
static_assert(std::is_same_v<decltype(::tpy::param_pass_last<int32_t>(std::declval<int32_t&>())),
                             int32_t&>);
static_assert(std::is_same_v<decltype(::tpy::open_local_last<Pinned>(std::declval<Pinned&>())),
                             Pinned&>);
// An open-result local's last use copies at a value storage type, as the
// twin's local does; only an owned value storage moves.
static_assert(std::is_same_v<decltype(::tpy::open_local_last<int32_t>(std::declval<int32_t&>())),
                             int32_t&>);
static_assert(std::is_same_v<decltype(::tpy::open_local_handoff<int32_t>(std::declval<int32_t&>())),
                             int32_t&&>);
static_assert(std::is_same_v<decltype(::tpy::open_local_handoff<Pinned>(std::declval<Pinned&>())),
                             Pinned&>);
static_assert(std::is_same_v<decltype(::tpy::open_local_last<own_arg<std::string>>(
                                 std::declval<std::string&>())),
                             std::string&&>);
// A const source (a `readonly[T]` parameter) keeps its const at a reference
// `T`'s return; a value `T` returns the value.
static_assert(std::is_same_v<decltype(::tpy::param_to_return<Point>(std::declval<const Point&>())),
                             const Point&>);
static_assert(std::is_same_v<decltype(::tpy::param_to_return<Point>(std::declval<Point&>())),
                             Point&>);

// Copied, never moved: a value whose copy and move are counted.
struct Counted {
    static inline int copies = 0;
    static inline int moves = 0;
    int v = 0;
    Counted() = default;
    Counted(const Counted& o) : v(o.v) { ++copies; }
    Counted(Counted&& o) noexcept : v(o.v) { ++moves; o.v = -1; }
};
}  // namespace owned_tag
template<> struct tpy::is_value_type<owned_tag::Counted> : std::true_type {};

namespace {
// A body's staged or frame-field return at its last use copies at a value
// `T` (the twin's `V __tpy_ret_0 = x;`); only the direct `return x;`, spelled
// through `param_handoff`, moves.
void return_last_copies() {
    using owned_tag::Counted;
    Counted c;
    c.v = 7;
    Counted r = ::tpy::param_to_return_last<Counted>(c);
    check(Counted::copies == 1 && Counted::moves == 0 && c.v == 7 && r.v == 7,
          "param_to_return_last copies a value T");
    Counted m = ::tpy::param_to_return<Counted>(::tpy::param_handoff<Counted>(c));
    check(Counted::copies == 1 && Counted::moves == 1 && m.v == 7,
          "the direct return moves a value T");
}
}  // namespace

int main() {
    slot_spelling();
    accessor_constness();
    collect_allocations();
    return_last_copies();
    if (failures != 0) {
        std::printf("%d yield-slot form check(s) failed\n", failures);
        return 1;
    }
    std::printf("yield slot forms OK\n");
    return 0;
}
