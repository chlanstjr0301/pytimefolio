# pytimefolio

Unofficial asyncio API wrapper for Timefolio's **Road to Fund Manager** paper-trading competition
(`contest.timefolio.net`). It reproduces the requests the contest's own web client makes; every
behaviour it relies on was measured and is documented in [`report/main.pdf`](report/main.pdf).

> **Unofficial.** Timefolio publishes no API and does not support this package. The organiser has
> told us that automated orders are not prohibited; check the current competition rules yourself before
> using it.

The organiser has stated that automated orders are not prohibited, but an IP address that places
100 or more orders a day, or sends bursts of traffic, may be blocked. The wrapper caps itself at
80 orders a day and spaces write requests 1 s apart. Do not raise the daily cap or shorten the
1 s spacing. Several users behind one IP address (a campus or office network) share that limit.

## Install

```bash
git clone https://github.com/chlanstjr0301/pytimefolio && cd pytimefolio
python -m venv ~/.venvs/tf && . ~/.venvs/tf/bin/activate   # under WSL keep the venv on the Linux side
pip install -e .
```

## Layout

| Path | Contents |
|---|---|
| `src/pytimefolio/` | the package: `transport`, `auth`, `rest`, `state`, `hub`, `orders`, `client` |
| `examples/` | `quickstart.py`, `guidelines.py`, `rebalance_dry_run.py` (none sends orders) |
| `tests/` | offline unit tests; `tests/live/` holds checks against the real server |
| `research/` | the measurement scripts behind the report |
| `docs/report/` | the report (LaTeX source and PDF); `docs/api.md` lists the endpoints |

## Use

```python
import asyncio, logging
from pytimefolio import Timefolio

async def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)       # httpx logs every URL at INFO
    async with Timefolio(ctst_id=91, email="...", password="...") as tf:
        print(tf.weights())                                     # {"A005930": 9.46, ...}
        for o in tf.plan({"A005930": 10.0, "A079550": 0.0}, limit_idx=5):
            print(o)                                            # dry run: nothing is sent
        ids = await tf.rebalance({"A005930": 10.0}, limit_idx=5, hm0="09:00", hm1="09:30")
        print([await tf.wait(i) for i in ids])

asyncio.run(main())
```

- `wei` on an order is the order size in % of NAV, not a target. `plan()` and `rebalance()` take
  targets and convert them.
- Price: exactly one of `limit_prc` (limit price), `limit_idx` (n ticks on the opposite side,
  `-n` for own side), `stop_prc`. Timing: `hm0` only = immediate; `hm0` and `hm1` = spread over
  the window.
- A sell is sent as `ls = L`, `ex = X`; the server records it with `sgn = -1`. The share count
  (`qty2ord`) is rounded down, so an order smaller than one share's weight may come out as 0 shares.
- `ModifyOrder` and `DeleteOrder` are not wrapped yet: they have not been tested against the server.

## Credentials

After the first login the cookies are kept in `~/.config/pytimefolio/session.json` (mode 0600)
and later starts renew from them without logging in. Logging out does **not** revoke them on the
server, so treat this file like a password and keep it out of the repository.

## Tests

```bash
python -m unittest discover -v tests     # offline, no server needed; this is what CI runs
```

`tests/live/` holds scripts that use your account against the real server; three of them place
(and immediately stop) real orders. See `tests/live/README.md`.
