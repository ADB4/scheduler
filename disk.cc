// disk.cc -- disk scheduler
//
// Usage: disk <max_disk_queue> <input_file_0> <input_file_1> ...
//
// Each input file lists track numbers (one per line) that the corresponding
// requester thread will request. A single service thread services requests
// in SSTF order, keeping the queue as full as possible.

#include <iostream>
#include <fstream>
#include <string>
#include <vector>
#include <cstdlib>
#include "thread.h"
#include <algorithm>


// ----- shared state (protected by monitor_lock) -----
// a c++ mutex is a synchronization primitive used to protect shared data from being accessed by multiple threads simultaneously
static mutex monitor_lock;

static int max_disk_queue;
static int num_requesters;
static int living_requesters;     // requesters that may still issue requests
static int current_track = 0;     // disk head position

// Each requester has at most one outstanding request at a time (synchronous).
// pending[r] = track if requester r has an unserviced request, -1 otherwise.
static std::vector<int> pending;
static std::vector<cv*> requester_cv;  // signaled when requester r's request is serviced
static cv queue_state_changed;    // wakes the service thread

// ----- requester thread -----
struct requester_arg {
    int id;
    std::string filename;
};

void requester_thread(void *a) {
    requester_arg *ra = (requester_arg *) a;
    std::ifstream in(ra->filename);
    int track;

    while (in >> track) {
        monitor_lock.lock();

        pending[ra->id] = track;
        std::cout << "requester " << ra->id << " track " << track << std::endl;

        queue_state_changed.signal();
        while (pending[ra->id] != -1) {
            requester_cv[ra->id]->wait(monitor_lock);
        }
        monitor_lock.unlock();
    }

    monitor_lock.lock();
    --living_requesters;
    queue_state_changed.signal();
    // TODO: --living_requesters; signal queue_state_changed
    monitor_lock.unlock();

    delete ra;
}



// ----- service thread -----
void service_thread(void *) {
        // helper function to get pending count
    auto pending_count = [&]() {
        int n = 0;
        for (int t: pending) if (t != -1) ++n;
        return n;
    };
    monitor_lock.lock();

    while (true) {
        // 1. Termination check: are we done forever?
        if (living_requesters == 0) break;
        // 2. Wait until the queue is as full as it can get.
        int target = std::min(living_requesters, max_disk_queue);
        while (pending_count() < target) {
            queue_state_changed.wait(monitor_lock);
            target = std::min(living_requesters, max_disk_queue);
        }
        // jump back to top-of-loop termination check
        if (living_requesters == 0) continue;
        // 3. Pick the SSTF request from pending[].
        // scan pending for the requester whose track is closest to current_track
        // tie-break deterministically by lower requester id
        int closest_requester = -1;
        int closest_distance = 0;
        for (int r = 0; r < num_requesters; ++r) {
            if (pending[r] == -1) continue;
            int distance = std::abs(pending[r] - current_track);
            if (closest_requester == -1 || distance < closest_distance) {
                closest_distance = distance;
                closest_requester = r;
            }
        }
        int track = pending[closest_requester];
        std::cout << "service requester " << closest_requester << " track " << pending[closest_requester] << std::endl;
        current_track = track;
        pending[closest_requester] = -1;
        requester_cv[closest_requester]->signal();
        // 4. Service it: print, update current_track, clear pending[r].
        // 5. Wake the requester whose request we just finished.
    }
    monitor_lock.unlock();
}

// ----- startup: runs in the initial thread created by cpu::boot -----
// cpu::boot takes a single void* arg, so we stash argc/argv in globals.
static int g_argc;
static char **g_argv;

void start(void *) {
    max_disk_queue = atoi(g_argv[1]);
    num_requesters = g_argc - 2;
    living_requesters = num_requesters;
    pending.assign(num_requesters, -1);
    for (int i = 0; i < num_requesters; ++i) {
        requester_cv.push_back(new cv());   // cv has deleted copy/move, so heap-allocate
    }

    thread svc(service_thread, nullptr);

    std::vector<thread*> reqs;
    for (int i = 0; i < num_requesters; ++i) {
        auto *ra = new requester_arg{i, g_argv[i + 2]};
        reqs.push_back(new thread(requester_thread, ra));
    }

    for (auto *t : reqs) { t->join(); delete t; }
    svc.join();

    for (auto *c : requester_cv) delete c;
}

int main(int argc, char *argv[]) {
    g_argc = argc;
    g_argv = argv;
    cpu::boot((thread_startfunc_t) start, nullptr, 0);
}