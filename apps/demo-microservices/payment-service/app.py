"""Demo payment-service. Owner: M1. K8s cluster owner: M4.

v1 (healthy):  low latency, ~0% 5xx.
v2 (faulty):   injected latency + ~14% 5xx, simulating a bad deployment.

Switch via env: VERSION=v1|v2, FAULT_LATENCY_MS, FAULT_5XX_RATE
"""
import os
import random
import time

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

VERSION = os.getenv("VERSION", "v1")
FAULT_LATENCY_MS = int(os.getenv("FAULT_LATENCY_MS", "600" if VERSION == "v2" else "0"))
FAULT_5XX_RATE = float(os.getenv("FAULT_5XX_RATE", "0.14" if VERSION == "v2" else "0.0"))

app = FastAPI(title="payment-service")


@app.get("/health")
def health():
    return {"status": "ok", "service": "payment-service", "version": VERSION}


@app.post("/pay")
def pay(response: Response):
    if FAULT_LATENCY_MS:
        time.sleep(FAULT_LATENCY_MS / 1000.0)
    if random.random() < FAULT_5XX_RATE:
        response.status_code = 500
        return {"ok": False, "error": "simulated v2 failure", "version": VERSION}
    return {"ok": True, "version": VERSION}


@app.get("/version")
def version():
    return {"service": "payment-service", "version": VERSION}
