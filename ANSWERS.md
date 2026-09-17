# Log Investigation - Missing Orders

## 1. When did the problem start?

The first failed checkout job shows up at 2026-07-02 14:32:42.692

```
2026-07-02 14:32:40.073 INFO [request] method=POST path=/checkout status=202 latency_ms=36 user_id=59787 request_id=16ce72300cf58a32
2026-07-02 14:32:42.692 ERROR [worker] upstream call failed request_id=16ce72300cf58a32 err=ECONNRESET upstream=10.0.3.44:8443 (retries exhausted)
```

I checked every checkout request before this time, and all of them have a matching "job completed" line in worker.log. After this time, some checkouts stop getting that "job completed" line. So this is the exact point where things start going wrong.

The last failure I can see is at 23:59:54, right at the end of the log file. So it looks like the problem was still going on when the logging stopped; it did not fix itself during the day.

## 2. Which endpoint is affected?

`POST /checkout`

There are only three request types that create a background job: `/login`, `/checkout`, and `/orders`. I checked all three, and only `/checkout` has requests that never get finished by the worker.

```
/login:    10044 total, 0 never completed
/orders:    7998 total, 0 never completed
/checkout:  9875 total, 2385 never completed
```

/orders works fine the whole day. So this is not a problem with the worker in general; it is something specific to what happens during checkout.

## 3. What do the failing requests have in common?

Every single one of the 2385 checkout requests that never finished has this same error next to it in worker.log:

```
ERROR [worker] upstream call failed request_id=<id> err=ECONNRESET upstream=10.0.3.44:8443 (retries exhausted)
```

Same error every time (ECONNRESET), same address every time (10.0.3.44:8443). So while the worker is handling a checkout job, it tries to call this other service, the connection gets dropped, it retries a few times, then gives up. Since the web request already sent back a 202 (accepted) before any of this happens, the customer never sees an error. The order just quietly never gets created.

Some numbers here:
- 2385 "upstream call failed" errors in worker.log, all pointing to the same address
- 2385 checkout requests with no "job completed" line
- These two lists match exactly, one to one, by request_id

It is also not a total outage. After 14:32, checkout traffic keeps coming in normally, but only about 42% of it fails:

```
checkouts after 14:32:40: 5627
failed after 14:32:40:    2385  (42.4%)
checkouts before 14:32:40: 4248
failed before 14:32:40:      0
```

The failure rate stays around 40 to 45 percent every hour from 14:00 all the way to 23:00. So this looks more like one bad server or a flaky connection somewhere, rather than the whole service being down.

## 4. How many distinct users were affected?

2335 different users had at least one checkout that silently failed. Since there are 2385 failed checkouts total, that means around 50 of these users were hit more than once.

## Bonus: what likely caused it

The clue is right there in worker.log. Every failed checkout job gets an ECONNRESET when calling 10.0.3.44:8443, and only checkout jobs ever call this address, not orders or login. So whatever that address is, probably a payment service or something that checks inventory, it started dropping connections at 14:32:40 and kept doing it on and off for the rest of the day.

The bigger problem, though, is how this fails silently. The web app tells the customer their order was accepted right away, before the background job even runs. So when the job fails later, nothing tells the user, and nothing shows up as an error in web.log either. The only trace of it is in worker.log. That is probably why nobody noticed until customers started complaining.

I also noticed a bunch of unrelated errors from something called metrics-worker, about failing to upload analytics data. There are 793 of these spread across the entire day, starting from midnight, way before the checkout problem began. They do not share any request_id with the checkout failures, and the timing does not line up either, so this is a separate, pre-existing issue that has nothing to do with the missing orders.

## How I investigated this

- First, I just looked at both files to understand the format, what fields each line has, timestamp, level, path, request_id, and so on.
- Counted requests by endpoint and status code to get a feel for normal traffic before hunting for anything broken.
- Noticed 1500 WARN lines about slow database queries spread evenly through the whole day. Checked the timing; they do not line up with when the incident starts, so ruled these out as unrelated noise.
- Also checked the 404s (product not found) and 401s (bad login on /api/user). Same thing, spread evenly all day, not related.
- Since the complaint was "orders placed but missing," it seemed like an async job problem, so I matched request_id between web.log (checkout and orders requests) and worker.log (job completed lines) to find requests that never got a matching completion.
- Found 2385 checkout requests with no completion, all starting at 14:32:40, and zero missing before that time.
- Checked /orders and /login the same way. Both had every single job complete, so the problem is specific to checkout jobs only.
- Found the matching cause, 2385 "upstream call failed" errors with ECONNRESET going to 10.0.3.44:8443, matching the missing checkout request_ids exactly.
- Checked the failure rate hour by hour to see if it was a full outage or partial. It stayed steady around 40 to 45 percent, which points to a partial or flaky problem rather than a hard crash.
- Double checked the metrics-worker errors were unrelated, since they show up all day long, including before and after the checkout issue, and never share a request_id with it.
