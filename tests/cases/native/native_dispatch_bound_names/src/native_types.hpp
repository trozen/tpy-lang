#pragma once
#include <cstdint>
#include <vector>

namespace nb {

struct Item {
    int32_t n;
};

// Three containers over the same storage, one per `__getitem__` binding
// form: a renamed member, a cpp_template, a free function.
struct Bag {
    std::vector<Item> items{{10}, {20}, {30}};
    Item& at(int32_t i) { return items[static_cast<std::size_t>(i)]; }
    const Item& at(int32_t i) const { return items[static_cast<std::size_t>(i)]; }
    // Adds each of `o`'s counts to this bag's.
    void merge(const Bag& o) {
        for (std::size_t i = 0; i < items.size(); ++i) items[i].n += o.items[i].n;
    }
};

inline Item& bag_get(Bag& b, int32_t i) { return b.at(i); }
inline const Item& bag_get(const Bag& b, int32_t i) { return b.at(i); }

// An element-typed container whose `__getitem__` stub binds `at`; it has
// no `__getitem__` member, so only the bound name builds.
template <typename T>
struct Ring {
    std::vector<T> items;
    auto begin() { return items.begin(); }
    auto end() { return items.end(); }
    auto begin() const { return items.begin(); }
    auto end() const { return items.end(); }
    std::size_t size() const { return items.size(); }
    T& at(int32_t i) { return items[static_cast<std::size_t>(i)]; }
    const T& at(int32_t i) const { return items[static_cast<std::size_t>(i)]; }
    void push(T v) { items.push_back(std::move(v)); }
};

// Reached through a `Ptr`: a renamed property getter and an argument-taking
// renamed method.
struct Root {
    Item item{40};
    Item& top() { return item; }
    int32_t pick(int32_t i) const { return item.n + i; }
    void bump() { item.n += 1; }
};

// A C++ enum bound as a @native value type, passed to a template method.
enum class Kind : int32_t { First = 0, Second = 1 };
inline Kind second_kind() { return Kind::Second; }

}  // namespace nb
