# Changelog

## 0.1.0 (2026-11-24)

First version, tested against the live server during the session on 24 Nov 2026.

- `Timefolio` client: renews from a stored session (logs in only when that fails or the cookies
  have less than a day left), selects the live portfolio, runs the live feed in the background.
- Live feed: SignalR polling every 3 s, one request in flight, snapshot/delta merge verified equal
  to same-`t` snapshots in 15 of 15 rounds.
- Orders: buy, sell, stop (`CancelOrderBatch`), wait until `Done`; order IDs found in the feed;
  80 orders a day and 1 s between writes; no blind resend.
- `plan()` / `rebalance()`: target weights to orders, stock caps by water-filling, rejects totals
  above 100%, sector sums above max(2 x market weight, 10%) and stocks outside the universe; drops
  orders smaller than one share.
- `guidelines()`: stock, sector, small-cap and weekly-turnover status plus the server's violation count.
- Not wrapped yet: `ModifyOrder`, `DeleteOrder` (untested against the server).
