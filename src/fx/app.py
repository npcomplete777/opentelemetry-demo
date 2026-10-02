"""fx: FastAPI currency-conversion service for the OBI showcase.

Async on uvloop, served over HTTPS (self-signed). Each request fans out with
asyncio.gather() to two rate lookups on the Spring Boot warehouse service.
Whether those outgoing calls are linked to the incoming request depends on
OBI's asyncio context tracking, which needs uvloop and an unstripped
libpython: the same code is built on python:3.12 (fx-full) and
python:3.12-slim (fx-slim) to show the difference.

No OpenTelemetry or other telemetry package is installed.
"""
import asyncio
import os

import httpx
from fastapi import FastAPI

WAREHOUSE = os.environ.get("WAREHOUSE_URL", "http://warehouse:8080")
app = FastAPI()
client = httpx.AsyncClient(base_url=WAREHOUSE, timeout=5.0)


async def rate(ccy: str) -> float:
    r = await client.get(f"/api/rates/{ccy}")
    r.raise_for_status()
    return r.json()["rate"]


@app.get("/convert")
async def convert(amount: float, ccy: str = "EUR"):
    target, usd = await asyncio.gather(rate(ccy), rate("USD"))
    return {"amount": amount, "ccy": ccy,
            "converted": round(amount * target / usd, 2),
            "variant": os.environ.get("FX_VARIANT", "unknown")}


@app.get("/healthz")
async def healthz():
    return {"ok": True}
