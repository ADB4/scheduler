#!/usr/bin/env python3
"""
Checker for EECS 482 Project 1 disk scheduler output.

Usage:
    ./disk MAX_QUEUE in0 in1 in2 ... > output.txt
    python3 check_disk.py MAX_QUEUE output.txt in0 in1 in2 ...

Verifies:
  1. Output format (only "requester R track T" and "service requester R track T",
     plus the trailing "All CPUs suspended. Exiting." line).
  2. Each requester's requests appear in file order (both issue and service lines).
  3. Every service line is preceded by its matching request line.
  4. Synchronous requests: a requester cannot issue request N+1 before
     request N has been serviced.
  5. SSTF: when the service thread picks a request, no other request in the
     queue at that moment is closer to the current head.
  6. Queue fullness: the service thread only picks when the queue holds
     min(max_queue, living_requesters) requests.
  7. Every issued request is eventually serviced; nothing extra is serviced.

Exit code 0 on pass, 1 on any violation. Prints all violations found.
"""

import re
import sys
from collections import defaultdict


REQ_RE = re.compile(r"^requester (\d+) track (-?\d+)$")
SVC_RE = re.compile(r"^service requester (\d+) track (-?\d+)$")
TRAILER = "All CPUs suspended.  Exiting."
def parse_input_file(path):
    """Return list of track ints from one input file."""
    with open(path) as f:
        return [int(line.strip()) for line in f if line.strip()]


def parse_output(path):
    """
    Return list of events. Each event is a tuple:
        ("req", line_no, requester, track)   for "requester R track T"
        ("svc", line_no, requester, track)   for "service requester R track T"
    Raises ValueError on a malformed line.
    """
    events = []
    saw_trailer = False
    with open(path) as f:
        lines = [ln.rstrip("\n") for ln in f]

    for i, line in enumerate(lines, start=1):
        if line == "":
            continue
        if line == TRAILER:
            if saw_trailer:
                raise ValueError(f"line {i}: duplicate trailer line")
            saw_trailer = True
            continue
        if saw_trailer:
            raise ValueError(f"line {i}: output after trailer: {line!r}")

        # service must be checked first because "service requester ..."
        # also starts with the literal "requester" later in the string.
        m = SVC_RE.match(line)
        if m:
            events.append(("svc", i, int(m.group(1)), int(m.group(2))))
            continue
        m = REQ_RE.match(line)
        if m:
            events.append(("req", i, int(m.group(1)), int(m.group(2))))
            continue

        raise ValueError(f"line {i}: unrecognized output: {line!r}")

    if not saw_trailer:
        # Not fatal for correctness of the scheduler logic, but worth flagging.
        print("warning: output is missing the 'All CPUs suspended. Exiting.' "
              "trailer line. (This line is printed by the thread library; "
              "if it's missing, the program may have exited abnormally.)",
              file=sys.stderr)

    return events


def check(max_queue, output_path, input_paths):
    inputs = {r: parse_input_file(p) for r, p in enumerate(input_paths)}
    num_requesters = len(inputs)
    events = parse_output(output_path)

    violations = []

    def fail(msg):
        violations.append(msg)

    # ---- Per-requester file-order check (issue + service) ---------------
    issued_idx = defaultdict(int)   # requester -> next index in input file
    serviced_idx = defaultdict(int)
    # Track which (requester, sequence_number) pairs are currently in the queue.
    # We key by sequence number rather than track because the same track may
    # legitimately appear twice in one input file.
    in_queue = {}      # (requester, seq) -> track
    queue_order = []   # ordered list of (requester, seq) currently queued

    head = 0  # disk head starts at track 0
    living = set(range(num_requesters))  # requesters with unfinished requests

    # Helper: how many requesters still have at least one un-issued OR un-serviced request?
    def living_count():
        return sum(
            1 for r in range(num_requesters)
            if serviced_idx[r] < len(inputs[r])
        )

    for ev in events:
        kind, lineno, r, t = ev

        if r < 0 or r >= num_requesters:
            fail(f"line {lineno}: requester {r} out of range "
                 f"(have {num_requesters} input files)")
            continue

        if kind == "req":
            # Must match next un-issued request in this requester's file.
            seq = issued_idx[r]
            if seq >= len(inputs[r]):
                fail(f"line {lineno}: requester {r} issued track {t} but "
                     f"has no more requests in its input file")
                continue
            expected = inputs[r][seq]
            if t != expected:
                fail(f"line {lineno}: requester {r} issued track {t}, "
                     f"expected {expected} (request #{seq} from input file)")
                continue

            # Synchronous-request check: previous request must already be serviced.
            if seq > 0 and serviced_idx[r] < seq:
                fail(f"line {lineno}: requester {r} issued track {t} "
                     f"(request #{seq}) before its previous request "
                     f"(#{seq - 1}) was serviced")

            in_queue[(r, seq)] = t
            queue_order.append((r, seq))
            issued_idx[r] += 1

        else:  # kind == "svc"
            seq = serviced_idx[r]
            if seq >= len(inputs[r]):
                fail(f"line {lineno}: service of requester {r} track {t} "
                     f"but that requester has no outstanding requests")
                continue
            expected = inputs[r][seq]
            if t != expected:
                fail(f"line {lineno}: serviced requester {r} track {t}, "
                     f"expected {expected} (request #{seq} in file order; "
                     f"a requester's requests must be serviced in file order "
                     f"because they are issued synchronously)")
                # Don't continue; still try to remove something so we keep going.

            key = (r, seq)
            if key not in in_queue:
                fail(f"line {lineno}: service of requester {r} track {t} "
                     f"before that request was issued")
                serviced_idx[r] += 1
                continue

            # ---- SSTF check ---------------------------------------------
            # Among everything currently in the queue, the picked request
            # must minimize |track - head|.
            picked_dist = abs(t - head)
            best_dist = min(abs(tr - head) for tr in in_queue.values())
            if picked_dist != best_dist:
                # Find a closer alternative for the error message.
                closer = [(rr, ss, tr) for (rr, ss), tr in in_queue.items()
                          if abs(tr - head) < picked_dist]
                examples = ", ".join(
                    f"requester {rr} track {tr} (distance {abs(tr - head)})"
                    for rr, ss, tr in closer[:3]
                )
                fail(f"line {lineno}: SSTF violation. Head is at {head}, "
                     f"serviced requester {r} track {t} (distance {picked_dist}), "
                     f"but the queue contained closer request(s): {examples}")

            # ---- Queue-fullness check ----------------------------------
            # When the service thread picks, the queue should hold
            # min(max_queue, living_requesters_at_this_moment) requests.
            #
            # "Living" here means: requesters that still have requests
            # remaining to be issued OR currently in the queue. A requester
            # whose final request is in the queue is still alive at the
            # moment of this service decision.
            alive_now = sum(
                1 for rr in range(num_requesters)
                if issued_idx[rr] < len(inputs[rr])      # more to come
                or any(k[0] == rr for k in in_queue)     # one currently queued
            )
            target = min(max_queue, alive_now)
            current = len(in_queue)
            if current < target:
                fail(f"line {lineno}: queue-fullness violation. Service "
                     f"thread picked requester {r} track {t} with only "
                     f"{current} request(s) in queue, but {alive_now} "
                     f"requester(s) are still alive and max_queue is "
                     f"{max_queue}, so the queue should hold {target} "
                     f"before servicing")

            # Remove from queue, advance head.
            del in_queue[key]
            queue_order.remove(key)
            head = t
            serviced_idx[r] += 1

    # ---- Final completeness checks -----------------------------------------
    for r in range(num_requesters):
        if issued_idx[r] != len(inputs[r]):
            fail(f"requester {r}: only {issued_idx[r]} of "
                 f"{len(inputs[r])} requests were issued")
        if serviced_idx[r] != len(inputs[r]):
            fail(f"requester {r}: only {serviced_idx[r]} of "
                 f"{len(inputs[r])} requests were serviced")

    if in_queue:
        leftover = ", ".join(
            f"requester {r} track {t}" for (r, _), t in in_queue.items()
        )
        fail(f"requests left in queue at end of output: {leftover}")

    return violations


def main():
    if len(sys.argv) < 4:
        print("usage: check_disk.py MAX_QUEUE output.txt in0 [in1 ...]",
              file=sys.stderr)
        sys.exit(2)

    try:
        max_queue = int(sys.argv[1])
    except ValueError:
        print(f"MAX_QUEUE must be an integer, got {sys.argv[1]!r}",
              file=sys.stderr)
        sys.exit(2)

    output_path = sys.argv[2]
    input_paths = sys.argv[3:]

    try:
        violations = check(max_queue, output_path, input_paths)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    if violations:
        print(f"FAIL: {len(violations)} violation(s) found:\n")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)

    print("PASS: output is consistent with the disk-scheduler spec.")
    sys.exit(0)


if __name__ == "__main__":
    main()
