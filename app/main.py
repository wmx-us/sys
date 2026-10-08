"""FastAPI 应用入口及全局生命周期/错误处理。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import SessionLocal, create_schema
from app.models import User
from app.seed import ensure_demo_data
from app.security import read_session_token
from app.web import _context, router, templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时准备持久化结构和幂等演示数据。"""

    create_schema()
    ensure_demo_data()
    yield


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(router)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> HTMLResponse:
    """把 HTTP 异常统一渲染为用户可理解的页面，并保留登录态导航。"""

    if exc.status_code in {301, 302, 303, 307, 308} and exc.headers:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(exc.headers["Location"], status_code=exc.status_code)
    current_user = None
    user_id = read_session_token(request.cookies.get("session"))
    if user_id is not None:
        with SessionLocal() as db:
            current_user = db.get(User, user_id)
            if current_user is not None:
                db.expunge(current_user)
    return templates.TemplateResponse(
        "error.html",
        _context(
            request,
            current_user,
            title=f"错误 {exc.status_code}",
            error=str(exc.detail),
            status_code=exc.status_code,
        ),
        status_code=exc.status_code,
    )
