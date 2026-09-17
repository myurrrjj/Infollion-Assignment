# Goes through web.log and worker.log and finds checkout requests that
# never got finished by the worker. This is how I found the missing orders.
#
# Run it like this:
#   python3 analyze_logs.py web.log worker.log

import sys
import re
from collections import defaultdict


def read_web_log(path):
    # pulls out the fields we need from each line in web.log
    requests = []
    with open(path) as file:
        for line in file:
            path_match = re.search(r"path=(\S*)", line)
            id_match = re.search(r"request_id=(\S+)", line)
            user_match = re.search(r"user_id=(\d+)", line)

            if not (path_match and id_match):
                continue

            requests.append({
                "timestamp": line[:23],
                "path": path_match.group(1),
                "request_id": id_match.group(1),
                "user_id": user_match.group(1) if user_match else None,
            })
    return requests


def read_worker_log(path):
    # separates worker.log into two dicts: jobs that finished, and jobs
    # that failed while calling the other service
    completed = {}
    failed = {}

    with open(path) as file:
        for line in file:
            id_match = re.search(r"request_id=(\S+)", line)
            if not id_match:
                continue

            request_id = id_match.group(1)
            timestamp = line[:23]

            if "job completed" in line:
                completed[request_id] = timestamp

            elif "upstream call failed" in line:
                error_match = re.search(r"err=(\S+)", line)
                host_match = re.search(r"upstream=(\S+)", line)
                failed[request_id] = {
                    "timestamp": timestamp,
                    "error": error_match.group(1) if error_match else None,
                    "host": host_match.group(1) if host_match else None,
                }

    return completed, failed


def main():
    web_log_path = sys.argv[1]
    worker_log_path = sys.argv[2]

    web_requests = read_web_log(web_log_path)
    completed, failed = read_worker_log(worker_log_path)

    # these are the only 3 endpoints that create a background job
    job_endpoints = {"/checkout", "/orders", "/login"}
    job_requests = [r for r in web_requests if r["path"] in job_endpoints]

    # count how many of each endpoint never got a "job completed" line
    counts_by_endpoint = defaultdict(lambda: {"total": 0, "stuck": 0})
    for r in job_requests:
        counts_by_endpoint[r["path"]]["total"] += 1
        if r["request_id"] not in completed:
            counts_by_endpoint[r["path"]]["stuck"] += 1

    print("requests per endpoint, and how many never finished:")
    for path, info in counts_by_endpoint.items():
        print(f"  {path}: {info['total']} total, {info['stuck']} never finished")

    # now zoom in on the stuck checkout requests specifically
    stuck_checkouts = [
        r for r in job_requests
        if r["path"] == "/checkout" and r["request_id"] not in completed
    ]
    stuck_checkouts.sort(key=lambda r: r["timestamp"])

    print()

    if not stuck_checkouts:
        print("no stuck checkouts found")
        return

    print(
        f"first stuck checkout: {stuck_checkouts[0]['timestamp']} "
        f"(request_id={stuck_checkouts[0]['request_id']})"
    )
    print(
        f"last stuck checkout:  {stuck_checkouts[-1]['timestamp']} "
        f"(request_id={stuck_checkouts[-1]['request_id']})"
    )
    print(f"total stuck checkouts: {len(stuck_checkouts)}")

    # check whether these all line up with an "upstream call failed" error
    matching_errors = [
        failed[r["request_id"]]
        for r in stuck_checkouts
        if r["request_id"] in failed
    ]
    error_types = {e["error"] for e in matching_errors}
    hosts = {e["host"] for e in matching_errors}

    print()
    print(f"of those, {len(matching_errors)} have a matching 'upstream call failed' error")
    print(f"error type(s) seen: {error_types}")
    print(f"host(s) they were trying to reach: {hosts}")

    distinct_users = {r["user_id"] for r in stuck_checkouts if r["user_id"]}
    print()
    print(f"distinct users affected: {len(distinct_users)}")


if __name__ == "__main__":
    main()
