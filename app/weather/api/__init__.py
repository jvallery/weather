# SPDX-License-Identifier: MIT
from fastapi import FastAPI

app = FastAPI(title="Weather", version="0.1.0", docs_url=None, redoc_url=None)


@app.get("/api/healthz")
@app.get("/healthz")
@app.get("/readyz")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "weather-api"}
