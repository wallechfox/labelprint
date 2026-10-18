"""LabelPrint —— 标签打印服务（FastAPI）。

路由：
    GET  /                  网页打印页
    GET  /health            健康检查
    GET  /config            页面默认参数
    POST /print             打印文本
    POST /print/image       打印图片（旧接口）
    POST /print/job         通用任务：文字 / 图片 / 两者
    GET  /logs              打印记录（分页，最新在前）
    POST /logs/clear        清空打印记录

鉴权：网址 ?token=xxx 或请求头 Authorization: Bearer xxx
"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.printer import (
    CHAR_H,
    CHAR_W,
    DEFAULT_ALIGN,
    DEFAULT_FEED,
    DEFAULT_SIZE,
    DITHER,
    LINE_SPACING,
    MAX_IMAGE_MB,
    PRINT_AREA,
    THRESHOLD,
    TS_ENABLE,
    TS_FORMAT,
    TS_POS,
    TS_SIZE,
    WIDTH_DOTS,
    clear_logs,
    layout,
    print_image,
    print_job,
    print_text,
    read_logs_page,
)

# ---------------- 配置 ----------------
API_TOKEN: str = os.getenv("LABEL_PRINT_TOKEN", "")
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

app = FastAPI(title="LabelPrint", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

security = HTTPBearer(auto_error=False)


def require_token(
    request: Request,
    cred: HTTPAuthorizationCredentials | None = Depends(security),
) -> None:
    """鉴权：支持网址 ?token=xxx，也兼容 Header 的 Bearer Token。

    未配置 Token 时一律拒绝（fail-closed）。
    """
    if not API_TOKEN:
        raise HTTPException(status_code=500, detail="服务端未配置 LABEL_PRINT_TOKEN，请检查 config.env")

    token = request.query_params.get("token", "")
    if not token and cred is not None and cred.scheme.lower() == "bearer":
        token = cred.credentials

    if not token:
        raise HTTPException(status_code=401, detail="缺少 Token（请在网址加 /?token=xxx，例如 http://服务器地址/?token=xxx）")
    if not secrets.compare_digest(token, API_TOKEN):
        raise HTTPException(status_code=403, detail="Token 无效")


# ---------------- 数据模型 ----------------
class PrintIn(BaseModel):
    text: str = Field(..., description="要打印的文本，多行用 \\n 分隔")
    source: str | None = Field(default="api", description="来源标记：api / web")
    align: int | None = Field(default=None, ge=0, le=2, description="对齐：0=左 1=居中 2=右")
    size: int | None = Field(default=None, ge=1, le=6, description="字号倍率：1~6")


# ---------------- 页面与基础接口 ----------------
@app.get("/", include_in_schema=False)
async def index(_=Depends(require_token)):
    """网页打印页，必须在网址上带 ?token=xxx 才能打开。"""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok", "service": "label-print"}


@app.get("/config", include_in_schema=False)
async def get_ui_config(_=Depends(require_token)):
    """给页面用的默认参数（打印宽度、字符度量、默认排版、时间戳）。"""
    return {
        "width_dots": PRINT_AREA,
        "paper_dots": WIDTH_DOTS,
        "char_w": CHAR_W,
        "char_h": CHAR_H,
        "line_spacing": LINE_SPACING,
        "max_image_mb": MAX_IMAGE_MB,
        "align": int(DEFAULT_ALIGN),
        "size": int(DEFAULT_SIZE),
        "feed": int(DEFAULT_FEED),
        "threshold": THRESHOLD,
        "dither": DITHER,
        "ts_enable": TS_ENABLE,
        "ts_pos": TS_POS,
        "ts_format": TS_FORMAT,
        "ts_size": int(TS_SIZE),
    }


@app.post("/preview")
async def preview(
    text: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    align: int = Form(default=0, ge=0, le=2),
    size: int = Form(default=2, ge=1, le=6),
    order: str = Form(default="text-first"),
    feed: int = Form(default=-1, ge=-1, le=20),
    rotate: int = Form(default=0),
    ts_enable: bool = Form(default=False),
    ts_pos: str = Form(default="bottom"),
    ts_fmt: str = Form(default="datetime"),
    ts_size: int = Form(default=1, ge=1, le=6),
    _=Depends(require_token),
):
    """排版预览：返回每个块的几何位置，前端照着画即可所见即所得。"""
    image_bytes: bytes | None = None
    if file is not None and file.filename:
        if not (file.content_type or "").startswith("image/"):
            raise HTTPException(status_code=400, detail="请上传图片文件")
        image_bytes = await file.read()
        if len(image_bytes) > MAX_IMAGE_MB * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"图片过大，上限 {MAX_IMAGE_MB}MB")

    try:
        lay = layout(
            text=text or None,
            image_bytes=image_bytes,
            align=align,
            size=size,
            order=order,
            feed=None if feed < 0 else feed,
            rotate=rotate,
            ts_enable=ts_enable,
            ts_pos=ts_pos,
            ts_fmt=ts_fmt,
            ts_size=ts_size,
        )
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})

    lay["status"] = "ok"
    return lay


# ---------------- 打印接口 ----------------
@app.post("/print")
async def do_print(payload: PrintIn, _=Depends(require_token)):
    # 保留原文的首尾换行（末尾回车就是一行空白），只用 strip() 判空
    text = payload.text or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="text 不能为空")

    ok, message = print_text(
        text,
        source=payload.source or "api",
        align=payload.align,
        size=payload.size,
    )
    if ok:
        return {"status": "ok", "message": "打印成功"}
    return JSONResponse(status_code=500, content={"status": "error", "message": message})


# ---------------- 图片打印接口 ----------------
@app.post("/print/image")
async def do_print_image(
    file: UploadFile = File(..., description="图片文件 jpg/png/gif/bmp/webp"),
    align: int = Form(default=0, ge=0, le=2),
    dither: bool = Form(default=False, description="true=抖动(照片) false=阈值(线条图)"),
    _=Depends(require_token),
):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="请上传图片文件")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="图片内容为空")
    if len(data) > MAX_IMAGE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"图片过大，上限 {MAX_IMAGE_MB}MB")

    ok, message = print_image(data, source="web", align=align, dither=dither)
    if ok:
        return {"status": "ok", "message": "打印成功"}
    return JSONResponse(status_code=500, content={"status": "error", "message": message})


# ---------------- 混排任务接口（文字 + 图片，一次出纸） ----------------
@app.post("/print/job")
async def do_print_job(
    text: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    align: int = Form(default=0, ge=0, le=2),
    size: int = Form(default=2, ge=1, le=6),
    order: str = Form(default="text-first", description="text-first 先文字 / image-first 先图片"),
    feed: int = Form(default=-1, ge=-1, le=20, description="末尾空白行，-1=用配置默认"),
    rotate: int = Form(default=0, description="图片旋转：0/90/180/270（顺时针）"),
    dither: bool = Form(default=False),
    invert: bool = Form(default=False, description="反色：白底黑字 -> 黑底白字"),
    threshold: int = Form(default=-1, ge=-1, le=255, description="二值化阈值，-1=用配置默认"),
    brightness: float = Form(default=-1, ge=-1, le=3, description="亮度 0.5~2，-1=不调整"),
    contrast: float = Form(default=-1, ge=-1, le=3, description="对比度 0.5~2，-1=不调整"),
    ts_enable: bool = Form(default=False, description="是否打印当前时间"),
    ts_pos: str = Form(default="bottom", description="top=最上面 / bottom=最下面"),
    ts_fmt: str = Form(default="datetime", description="时间格式预设名"),
    ts_size: int = Form(default=1, ge=1, le=6, description="时间戳字号倍率"),
    no_log: bool = Form(default=False, description="无痕模式：本次不写入打印记录"),
    _=Depends(require_token),
):
    image_bytes: bytes | None = None
    if file is not None and file.filename:
        if not (file.content_type or "").startswith("image/"):
            raise HTTPException(status_code=400, detail="请上传图片文件")
        image_bytes = await file.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="图片内容为空")
        if len(image_bytes) > MAX_IMAGE_MB * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"图片过大，上限 {MAX_IMAGE_MB}MB")

    ok, message = print_job(
        text=text or None,
        image_bytes=image_bytes,
        source="web",
        align=align,
        size=size,
        order=order,
        feed=None if feed < 0 else feed,
        rotate=rotate,
        dither=dither,
        invert=invert,
        threshold=None if threshold < 0 else threshold,
        brightness=1.0 if brightness < 0 else brightness,
        contrast=1.0 if contrast < 0 else contrast,
        ts_enable=ts_enable,
        ts_pos=ts_pos,
        ts_fmt=ts_fmt,
        ts_size=ts_size,
        no_log=no_log,
    )
    if ok:
        return {"status": "ok", "message": "打印成功"}
    return JSONResponse(status_code=500, content={"status": "error", "message": message})


# ---------------- 日志接口 ----------------
@app.get("/logs")
async def get_logs(
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(default=10, ge=1, le=100, description="每页条数"),
    _=Depends(require_token),
):
    """打印记录，按页返回（最新在前）。"""
    items, total = read_logs_page(page, page_size)
    pages = max(1, (total + page_size - 1) // page_size)
    return {
        "status": "ok",
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
        "count": len(items),
        "items": items,
    }


@app.post("/logs/clear")
async def clear_log_records(_=Depends(require_token)):
    """一键清空打印记录。"""
    n = clear_logs()
    if n < 0:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "清空打印记录失败，请检查日志文件权限"},
        )
    return {"status": "ok", "cleared": n}
