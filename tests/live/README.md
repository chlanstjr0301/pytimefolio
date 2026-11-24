# Live checks

Scripts that talk to `contest.timefolio.net` with your own account. They are named `live_*.py` so
`python -m unittest discover tests` never picks them up, and CI never runs them.

Run them from the repository root after `pip install -e .` and one login (the first run of
`live_auth.py` reads `EMAIL` and `PW` from `.env` and stores the session in
`~/.config/pytimefolio/session.json`).

| Script | Sends orders | Checks |
|---|---|---|
| `live_transport.py` | no | HTTP/2, connection reuse, server clock offset |
| `live_auth.py` | no | login once, then renewal from the stored session |
| `live_rest.py` | no | portfolio rule, Session, Universe, Trades, Orders, ValidateProduct |
| `live_hub.py` | no | snapshot then deltas every 3 s |
| `live_hub_verify.py` | no | merged deltas equal a snapshot taken at the same `t` |
| `live_orders.py` | **yes** | buy 0.1% of A005930 at 20% below market, stop it, wait until Done |
| `live_sell.py` | **yes** | sell 0.1% of A079550 at 20% above market, stop it, wait until Done |
| `live_rounding.py` | **yes** | buy 0.04% of A005930 (about 1.77 shares) at 20% below market, stop it; 1 share ordered means the server rounds down |

The three order scripts place real competition orders (limit prices far from the market, stopped after
3 s). Each counts against the organiser's limit of fewer than 100 orders a day. Read them before
running, and change the tickers to ones you hold.
