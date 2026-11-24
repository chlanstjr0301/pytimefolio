# Test: hub reconnects after a dropped connection and starts again from a snapshot

## Why
The offline suite (30 tests) covers transport, auth, the order gate and state merging. For the hub it only has unit checks
(`HubFreshnessTest`, and `HubTimeoutTest` for the per-request reply deadline and the stop on 403/429); nothing runs
`pytimefolio.hub.Hub` end to end against a server. The report (Section 4.4, threats to validity; Section 2.4, feed failure handling)
claims that after any error the feed closes, waits U(0, min(30, 2^n)) s, reconnects, renews the token first if needed,
and asks for a snapshot again with `lastT=0`. No test checks this. It is the largest gap the CS review found.

## What to build
A fake SignalR server with `websockets.serve` on localhost, plus a fake negotiate endpoint via `httpx.MockTransport`,
so `Hub` runs against it without the real site.

- Fake server speaks the protocol `Hub` expects: handshake `{"protocol":"json","version":1}` + `\x1e`, answers `{}` + `\x1e`;
  on `RequestPortfolio` replies `ReceivePortfolio` with a snapshot when `lastT == 0`, otherwise a delta, with `t` increasing.
- Point `Hub` at the fake server (`hub.WS_URL` can be patched; negotiate goes through `Auth.call`, so give `Auth` a
  `Transport(transport=httpx.MockTransport(...))` that returns `{"connectionToken": "x"}` and a token for `SilentRefresh`).
- Patch `random.uniform` (or `asyncio.sleep` in `hub`) so backoff does not slow the test.

## Cases (one test each)
1. Normal: first reply is a snapshot (`kind == "full"`), the next ones deltas; `hub.last_reply` advances.
2. Drop mid-stream: the server closes the socket after 2 replies. `stream()` must log a failure, reconnect, and the first
   reply after reconnecting must be a snapshot (the request carried `lastT == 0`). `hub.failures` resets to 0 afterwards.
3. Server error on reconnect: negotiate returns 502 once, then works. The loop must keep going (no exception escapes
   `stream()`), `failures` counts 1 then resets.
4. Null reply: the server sends `ReceivePortfolio` with `arguments: [null]`; the next request must carry `lastT == 0`.
5. Pings but no reply: the server answers the handshake and keeps sending pings (`{"type":6}`) every 0.2 s but never
   sends `ReceivePortfolio`. With a small `reply_timeout`, `stream()` must give up on that request, reconnect, and the
   next request must carry `lastT == 0`.
6. Staleness: stop the server for longer than `2*interval + reply_timeout` (use a small interval/timeout in the test);
   `hub.assert_fresh()` must raise `StaleFeed`.

## Done when
- New file `tests/test_hub.py`, runs in under about 5 s, passes in CI (Python 3.11 to 3.13), no network access.
- `python -m unittest discover tests` shows 30 + your tests, all passing.
- PR description lists which report claims the tests now back (Section 2.4 feed failure handling; D30).

## Before you start
- Branch from the latest `dev` (it includes your certifi fix from `main` and the hub timeout fix).

## Pointers
- Code: `src/pytimefolio/hub.py` (`connect`, `poll_once`, `stream`, `assert_fresh`), `src/pytimefolio/state.py`.
- Existing patterns: `tests/test_failures.py` (`MockTransport`, fakes, `asyncio.run`).
- Message framing: every SignalR message is one JSON object followed by the byte `0x1E`.
