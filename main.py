"""Instagram/Meta webhook web app entrypoint (not the Telegram polling worker)."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import db
from admin_routes import router as admin_router
from webhook_routes import router as webhook_router

app = FastAPI(title="BestHome IG DM Admin", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(webhook_router)
app.include_router(admin_router)


@app.on_event("startup")
async def on_startup():
    db.init_db()


@app.get("/health")
async def health():
    return JSONResponse({"ok": True})
