"""FastAPI application entrypoint for UltimaPay."""

from datetime import datetime

from fastapi import FastAPI


app = FastAPI(title="UltimaPay API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

