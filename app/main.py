from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import Base, engine
from app.api.events import router as events_router
from app.api.endpoints import router as endpoints_router
from app.api.deliveries import router as deliveries_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield
    finally:
        await engine.dispose()


app = FastAPI(
    title="Hook Forge API",
    description="Accept events and deliver signed webhooks to registered endpoints.",
    lifespan=lifespan,
)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.include_router(events_router)
app.include_router(endpoints_router)
app.include_router(deliveries_router)


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(static_dir / "index.html")


@app.get("/health", status_code=200)
async def health_check():
    return {"status": "ok"}
