#pragma once
#include <cstddef>
#include <cstdint>
#include <utility>
#include <vector>

struct Node {
    int32_t v;
    // Returns its ARGUMENT by reference, whatever the receiver's const-ness.
    Node& pick(Node& other) const { return other; }
};

// Both return the FIRST argument by reference.
Node& pick_ref(Node& a, Node& b);
Node& pick_copy(Node& a, Node& b);

// The first element, by reference.
Node& first(std::vector<Node>& items);

// An element-owning container whose `first` hands out its first element.
template <class T>
struct Bag {
    std::vector<T> items;
    auto begin() { return items.begin(); }
    auto end() { return items.end(); }
    auto begin() const { return items.begin(); }
    auto end() const { return items.end(); }
    std::size_t size() const { return items.size(); }
    void push(T v) { items.push_back(std::move(v)); }
    T& first() { return items[0]; }
    const T& first() const { return items[0]; }
};
