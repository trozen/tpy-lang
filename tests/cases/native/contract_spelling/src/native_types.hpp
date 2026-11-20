#pragma once
#include <cstddef>
#include <cstdint>
#include <utility>
#include <vector>

struct Cell {
    int32_t v;
};

// Both return their FIRST argument by reference.
inline Cell& pick_first(Cell& a, Cell&) { return a; }
inline Cell& pick_either(Cell& a, Cell&) { return a; }

// The first element, by reference.
inline Cell& first_of(std::vector<Cell>& items) { return items[0]; }

// An element-owning container: `put` replaces an element in place, `has`
// only compares its argument with the elements, `push` holds it.
template <class T>
struct Ring {
    std::vector<T> items;
    auto begin() { return items.begin(); }
    auto end() { return items.end(); }
    auto begin() const { return items.begin(); }
    auto end() const { return items.end(); }
    std::size_t size() const { return items.size(); }
    void push(T v) { items.push_back(std::move(v)); }
    void put(int32_t i, T v) { items[static_cast<std::size_t>(i)] = std::move(v); }
    bool has(const T& v) const {
        for (const auto& x : items) {
            if (x == v) return true;
        }
        return false;
    }
};
