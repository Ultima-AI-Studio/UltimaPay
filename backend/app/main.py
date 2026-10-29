"""FastAPI application entrypoint for UltimaPay."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI

from . import db as database
from .routes.payment import router as payment_router
from .services.payment_loop import payment_loop


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database.migrate()
    task = asyncio.create_task(payment_loop(), name="ultimapay-payment-loop")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="UltimaPay API", lifespan=lifespan)
app.include_router(payment_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

