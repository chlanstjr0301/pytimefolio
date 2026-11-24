# Research scripts

The measurements behind `docs/report/main.pdf`, kept so the results can be reproduced. They were
written before the package existed and do not import it. Run them from the repository root; most
read a curl cookie jar under `data/cookies/` and write to `logs/` (both git-ignored).

| Script | Report section | What it measures |
|---|---|---|
| `probe_feedback.py` | 2.1, 2.2 | TLS handshake, HTTP/2 settings, DNS, idle-connection limit, token tolerance |
| `signalr_raw.py` | 2.4 | raw SignalR client: negotiate, connect, handshake, `RequestPortfolio` |
| `lastT_probe.py` | 2.4 | the three hypotheses for when the hub sends a delta |
| `ws_expiry_probe.py` | 2.4 | an open WebSocket past its token's expiry |
| `async_lab.py` | n/a | asyncio exercises (event loop, TaskGroup, cancellation) |
| `market_probe.py` | 2.4, 2.2 | hub push during the session, delta sizes, parallel renewal |
| `logout_probe.py` | 2.2 | what `Auth/Logout` revokes (nothing on the server) |
| `preopen_order.py` | 4 | **places a real order** before the open and records its fills |

`preopen_order.py --live` sends a real contest order; `--monitor` only records. The organiser may
block an IP address that places 100 or more orders a day or sends bursts of traffic, so none of these
scripts loops on writes, and live-feed requests keep the browser's 3-second pace.
