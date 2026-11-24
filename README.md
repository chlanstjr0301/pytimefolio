# pytimefolio

Unofficial asyncio API wrapper for Timefolio's **Road to Fund Manager** paper-trading contest
(`contest.timefolio.net`). It reproduces the requests the contest's own web client makes; every
behaviour it relies on was measured and is documented in [`report/main.pdf`](report/main.pdf).

The organiser has stated that automated orders are not prohibited, but an account placing 100 or
more orders a day, or sending bursts of traffic, may have its IP address blocked. The wrapper caps
itself at 80 orders a day and spaces write requests 1 s apart. Do not lower these limits.

## Install

```bash
python -m venv ~/.venvs/tf && . ~/.venvs/tf/bin/activate   # keep the venv on the Linux side under WSL
pip install -e .
```

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
- Sells (`ex = X`) are not yet verified against the server and need `allow_untested=True`.

## Credentials

After the first login the cookies are kept in `~/.config/pytimefolio/session.json` (mode 0600)
and later starts renew from them without logging in. Logging out does **not** revoke them on the
server, so treat this file like a password and keep it out of the repository.

## Tests

```bash
python -m unittest discover -v tests     # offline, no server needed
```
