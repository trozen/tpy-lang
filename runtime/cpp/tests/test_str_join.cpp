// Join suffix sizing must save allocations without pre-scanning small outputs
// or revisiting user iterables.
// Checks remain active in release builds, where assert() would disappear.
#include <array>
#include <cstdio>
#include <cstdlib>
#include <expected>
#include <initializer_list>
#include <iterator>
#include <limits>
#include <new>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "tpy/tpy.hpp"

namespace {
bool measuring = false;
std::size_t allocations = 0;
int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

template<typename Source>
void one_allocation(const Source& source, std::string_view expected,
                    std::string_view separator = "::") {
    allocations = 0;
    measuring = true;
    auto joined = tpy::str_join(separator, source);
    measuring = false;
    check(allocations == 1, "one output allocation for builtin storage");
    check(joined == expected, "long output contents");
}

template<typename Source>
void no_allocation(const Source& source, std::string_view expected,
                   std::string_view separator = "|") {
    allocations = 0;
    measuring = true;
    auto joined = tpy::str_join(separator, source);
    measuring = false;
    check(allocations == 0, "small output needs no allocation");
    check(joined == expected, "small output contents");
}

struct Converted {
    std::string_view text;
    int* conversions;
    operator std::string_view() const {
        ++*conversions;
        return text;
    }
};

struct ForwardRange {
    std::array<std::string_view, 3> items{"a", "", "c"};
    mutable int begins = 0;
    auto begin() const { ++begins; return items.begin(); }
    auto end() const { return items.end(); }
};

struct DerivedVector : std::vector<std::string_view> {
    using std::vector<std::string_view>::vector;
    mutable int begins = 0;
    auto begin() const { ++begins; return std::vector<std::string_view>::begin(); }
};

struct DerivedString : std::string {
    using std::string::string;
};

template<typename T>
struct CustomAllocator {
    using value_type = T;
    CustomAllocator() = default;
    template<typename U>
    CustomAllocator(const CustomAllocator<U>&) {}
    T* allocate(std::size_t n) { return std::allocator<T>{}.allocate(n); }
    void deallocate(T* data, std::size_t n) { std::allocator<T>{}.deallocate(data, n); }
    template<typename U>
    bool operator==(const CustomAllocator<U>&) const { return true; }
};

using CustomVector = std::vector<std::string_view, CustomAllocator<std::string_view>>;

// Copying the iterator does not rewind its source.
struct InputRange {
    std::array<std::string_view, 3> items{"a", "", "c"};
    mutable std::size_t position = 0;
    mutable int begins = 0;
    struct Iterator {
        using value_type = std::string_view;
        using difference_type = std::ptrdiff_t;
        using iterator_concept = std::input_iterator_tag;
        const InputRange* source;
        const std::string_view& operator*() const { return source->items[source->position]; }
        Iterator& operator++() { ++source->position; return *this; }
        void operator++(int) { ++*this; }
        bool operator==(std::default_sentinel_t) const {
            return source->position == source->items.size();
        }
    };
    Iterator begin() const { ++begins; return {this}; }
    std::default_sentinel_t end() const { return {}; }
};

struct NonRange {
    std::array<std::string_view, 3> items{"a", "", "c"};
    std::size_t position = 0;
    int iter_calls = 0;
    int next_calls = 0;
    NonRange& __iter__() { ++iter_calls; return *this; }
    std::expected<std::string_view, tpy::StopIteration> __next__() {
        ++next_calls;
        if (position == items.size()) return tpy::make_unexpected(tpy::StopIteration{});
        return items[position++];
    }
};

static_assert(std::ranges::input_range<const InputRange>);
static_assert(!std::ranges::forward_range<const InputRange>);
static_assert(std::ranges::forward_range<const ForwardRange>);
static_assert(tpy::detail::join_sized_source<std::vector<std::string>>);
static_assert(tpy::detail::join_sized_source<std::array<tpy::String, 3>>);
static_assert(tpy::detail::join_sized_source<std::span<const std::string_view>>);
static_assert(tpy::detail::join_sized_source<std::initializer_list<std::string_view>>);
static_assert(!tpy::detail::join_sized_source<DerivedVector>);
static_assert(!tpy::detail::join_sized_source<ForwardRange>);
static_assert(!tpy::detail::join_sized_source<std::vector<Converted>>);
static_assert(!tpy::detail::join_sized_source<std::vector<DerivedString>>);
static_assert(!tpy::detail::join_sized_source<std::vector<const char*>>);
static_assert(!tpy::detail::join_sized_source<CustomVector>);

void checked_lengths() {
    const auto limit = std::numeric_limits<std::size_t>::max();
    check(tpy::detail::join_checked_add(0, 0, 0) == 0, "zero-sized boundary");
    check(tpy::detail::join_checked_add(3, 7, 10) == 10, "bounded exact length");
    check(tpy::detail::join_checked_add(limit - 1, 1, limit) == limit,
          "size_t exact boundary");
    for (const auto& [total, increment, maximum] :
         std::array<std::array<std::size_t, 3>, 4>{{
             {3, 8, 10}, {11, 0, 10}, {limit, 1, limit}, {limit - 1, 2, limit}}}) {
        try {
            (void)tpy::detail::join_checked_add(total, increment, maximum);
            check(false, "oversized join must throw");
        } catch (const std::length_error&) {
        } catch (...) {
            check(false, "oversized join exception type");
        }
    }
}
}  // namespace

void* operator new(std::size_t size) {
    if (measuring) ++allocations;
    if (void* p = std::malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }

int main() {
    check(tpy::str_join("|", std::vector<std::string>{}).empty(), "empty vector");
    check(tpy::str_join("|", std::array<std::string_view, 0>{}).empty(), "empty array");
    check(tpy::str_join("|", std::span<const std::string_view>{}).empty(), "empty span");
    check(tpy::str_join("|", std::initializer_list<std::string_view>{}).empty(),
          "empty initializer list");
    check(tpy::str_join("|", std::array<std::string_view, 1>{"a"}) == "a", "single item");
    check(tpy::str_join("|", std::array<std::string_view, 3>{"", "", ""}) == "||",
          "empty items still receive separators");
    check(tpy::str_join("", std::vector<std::string>{"a", "b", "c"}) == "abc",
          "empty separator");
    check(tpy::str_join("|", {"a", "", "c"}) == "a||c", "char pointer overload");
    check(tpy::str_join("|", {std::string_view("a"), std::string_view(""),
                              std::string_view("c")}) == "a||c", "view overload");
    const std::string nul("a\0b", 3), sep("\0|", 2);
    check(tpy::str_join(sep, std::vector<std::string>{nul, nul}) == nul + sep + nul,
          "embedded NUL items and separator");
    check(tpy::str_join("|", std::vector<std::string>{"\xc3\xa9", "\xed\x95\x9c"}) ==
          "\xc3\xa9|\xed\x95\x9c", "UTF-8 bytes");

    no_allocation(std::vector<std::string>{"a", "", "c"}, "a||c");
    no_allocation(std::array<std::string_view, 3>{"a", "", "c"}, "a||c");
    no_allocation(std::initializer_list<std::string_view>{"a", "", "c"}, "a||c");
    const std::array<std::string_view, 3> short_views{"a", "", "c"};
    no_allocation(std::span<const std::string_view>(short_views), "a||c");
    no_allocation(std::array<std::string_view, 0>{}, "");
    const std::string_view null_view;
    no_allocation(std::array<std::string_view, 3>{null_view, null_view, "c"},
                  "c", null_view);
    no_allocation(std::array<std::string_view, 3>{null_view, null_view, "c"},
                  "||c");
    no_allocation(std::array<std::string_view, 2>{null_view, null_view}, "", null_view);
    no_allocation(std::array<std::string_view, 1>{null_view}, "", null_view);
    // Exercise the same internal loop with observable conversions, without
    // admitting these elements to the public optimized path. Zero allocations
    // alone would not distinguish a prepass from a single traversal.
    int short_conversions = 0;
    const std::array<Converted, 3> short_counted{{{"a", &short_conversions},
                                               {"", &short_conversions},
                                               {"c", &short_conversions}}};
    check(tpy::detail::str_join_sized_range("|", short_counted.begin(),
                                          short_counted.end()) == "a||c",
          "small internal path contents");
    check(short_conversions == 3, "small outputs have no sizing prepass");

    // Capacity-derived boundaries work with different standard-library SSO
    // sizes: trigger on the first item, separator, or later item respectively.
    const auto initial_capacity = std::string{}.capacity();
    const std::string boundary(initial_capacity, 'x');
    no_allocation(std::array<std::string_view, 1>{boundary}, boundary);
    const std::string first_long(initial_capacity + 1, 'y');
    one_allocation(std::array<std::string_view, 1>{first_long}, first_long);
    one_allocation(std::array<std::string_view, 3>{boundary, "", "z"},
                   boundary + "::" + "::z");
    one_allocation(std::array<std::string_view, 3>{"x", first_long, "z"},
                   "x" + first_long + "z", "");
    const std::vector<std::string_view> empty_prefix(initial_capacity + 4, "");
    one_allocation(empty_prefix, std::string((empty_prefix.size() - 1) * 2, ':'));
    one_allocation(std::array<std::string_view, 3>{boundary, nul, nul},
                   boundary + sep + nul + sep + nul, sep);
    if (initial_capacity >= nul.size() + sep.size()) {
        const std::string near_boundary(initial_capacity - nul.size() - sep.size(), 'x');
        no_allocation(std::array<std::string_view, 2>{near_boundary, nul},
                      near_boundary + sep + nul, sep);
    }

    const std::string a(64, 'a'), b(64, 'b'), c(64, 'c');
    const auto expected = a + "::" + b + "::" + c;
    const std::vector<std::string> strings{a, b, c};
    const std::vector<tpy::String> string_objects{tpy::String(a), tpy::String(b),
                                                 tpy::String(c)};
    const std::vector<std::string_view> view_vector{a, b, c};
    const std::array<std::string, 3> string_array{a, b, c};
    const std::array<tpy::String, 3> owned{tpy::String(a), tpy::String(b), tpy::String(c)};
    std::array<std::string_view, 3> views{a, b, c};
    one_allocation(strings, expected);
    one_allocation(string_objects, expected);
    one_allocation(view_vector, expected);
    one_allocation(string_array, expected);
    one_allocation(owned, expected);
    one_allocation(views, expected);
    one_allocation(std::span<const std::string>(strings), expected);
    one_allocation(std::span<const tpy::String, 3>(owned), expected);
    one_allocation(std::span<const std::string_view>(views), expected);
    one_allocation(std::span<std::string_view, 3>(views), expected);
    one_allocation(std::initializer_list<std::string_view>{a, b, c}, expected);
    one_allocation(std::initializer_list<std::string>{a, b, c}, expected);
    one_allocation(std::initializer_list<tpy::String>{tpy::String(a), tpy::String(b),
                                                    tpy::String(c)}, expected);
    check(strings[0] == a && owned[0] == a, "operands retain their storage");
    check(tpy::str_join("::", CustomVector{a, b, c}) == expected,
          "custom allocator vector uses the generic fallback");
    const std::vector<const char*> pointers{a.c_str(), b.c_str(), c.c_str()};
    check(tpy::str_join("::", pointers) == expected, "long C string pointers");
    check(tpy::str_join("::", {a.c_str(), b.c_str(), c.c_str()}) == expected,
          "long C string initializer list");
    auto detached = tpy::str_join("|", strings);
    detached[0] = 'z';
    check(strings[0] == a, "result owns independent storage");

    int conversions = 0;
    const std::vector<Converted> converted{{"a", &conversions}, {"", &conversions},
                                          {"c", &conversions}};
    check(tpy::str_join("|", converted) == "a||c", "user conversion contents");
    check(conversions == 3, "user conversions happen once per element");
    const ForwardRange forward;
    check(tpy::str_join("|", forward) == "a||c", "custom forward range contents");
    check(forward.begins == 1, "custom forward range traversed once");
    const DerivedVector derived{"a", "", "c"};
    check(tpy::str_join("|", derived) == "a||c", "derived container contents");
    check(derived.begins == 1, "derived container traversed once");
    const InputRange input;
    check(tpy::str_join("|", input) == "a||c", "input-only range contents");
    check(input.begins == 1 && input.position == 3, "input-only range consumed once");
    NonRange non_range;
    check(tpy::str_join("|", non_range) == "a||c", "non-range iterable contents");
    check(non_range.iter_calls == 1 && non_range.next_calls == 4,
          "non-range iterable consumed once");
    checked_lengths();
    return failures != 0;
}
