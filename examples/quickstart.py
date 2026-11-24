"""Connect, read weights and guidelines, plan a rebalance without sending it.

    pip install -e .
    python examples/quickstart.py      # first run: put EMAIL and PW in the environment
"""
import asyncio
import logging
import os

from pytimefolio import Timefolio

CONTEST_ID = 91          # RFM round 13


async def main():
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    async with Timefolio(CONTEST_ID, os.getenv("EMAIL"), os.getenv("PW")) as tf:
        w = tf.weights()
        print(f"{len(w)} holdings, {sum(w.values()):.2f}% invested, {tf.orders_left()} orders left today")
        g = await tf.guidelines()
        print("weekly turnover:", g["turnover"]["this_week"], "% (minimum 5) | guidelines ok:", g["ok"])
        target = dict(w)                                   # replace with your strategy's targets (%)
        for o in tf.plan(target, limit_idx=5, hm0="09:00", hm1="09:30"):
            print("would send:", o)


asyncio.run(main())
