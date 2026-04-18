/**
 * TurboPython Runtime - Heap Queue (min-heap)
 *
 * Wraps C++ std::*_heap with std::greater to produce a min-heap matching
 * Python's heapq module semantics.
 */

#pragma once

#include <algorithm>
#include <functional>
#include <vector>

#include "core.hpp"

namespace tpy {

template<typename T>
inline void heap_push(std::vector<T>& heap, T item) {
    heap.push_back(std::move(item));
    std::push_heap(heap.begin(), heap.end(), std::greater<T>{});
}

template<typename T>
inline T heap_pop(std::vector<T>& heap) {
    if (heap.empty()) tpy_panic("heapq.heappop() called on an empty heap");
    std::pop_heap(heap.begin(), heap.end(), std::greater<T>{});
    T val = std::move(heap.back());
    heap.pop_back();
    return val;
}

template<typename T>
inline void heap_heapify(std::vector<T>& heap) {
    std::make_heap(heap.begin(), heap.end(), std::greater<T>{});
}

template<typename T>
inline T heap_replace(std::vector<T>& heap, T item) {
    if (heap.empty()) tpy_panic("heapq.heapreplace() called on an empty heap");
    std::pop_heap(heap.begin(), heap.end(), std::greater<T>{});
    T val = std::move(heap.back());
    heap.back() = std::move(item);
    std::push_heap(heap.begin(), heap.end(), std::greater<T>{});
    return val;
}

template<typename T>
inline T heap_pushpop(std::vector<T>& heap, T item) {
    // Optimized: if item <= heap[0], return item immediately without touching heap
    if (!heap.empty() && item <= heap.front()) {
        return item;
    }
    T val = std::move(heap.front());
    heap.front() = std::move(item);
    // Re-sift down to restore heap invariant
    std::make_heap(heap.begin(), heap.end(), std::greater<T>{});
    return val;
}

} // namespace tpy
