"""FastAPI 入口：航段时间清洗（DST 安全入仓）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from .compare import CompareInputError, compare_tzif_rules
from .solver import CsvFormatError, build_export_csv, solve_csv

BASE_DIR = Path(__file__).resolve().parent
SAMPLE_PATH = BASE_DIR.parent / "samples" / "dst_sample.csv"
COMPARE_SAMPLE_PATH = BASE_DIR.parent / "samples" / "tz_compare_sample.csv"
COMPARE_TZIF_DIR = BASE_DIR.parent / "samples" / "tzif"
INDEX_PATH = BASE_DIR / "static" / "index.html"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_TZIF_BYTES = 5 * 1024 * 1024  # 5 MB

app = FastAPI(title="航段时间清洗 · DST 安全入仓", version="1.0.0")


async def _read_csv_text(file: UploadFile) -> str:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件超过 10 MB 上限")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "文件不是合法的 UTF-8 编码 CSV")


async def _read_tzif_bytes(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_TZIF_BYTES:
        raise HTTPException(413, "TZif 文件超过 5 MB 上限")
    return data


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(INDEX_PATH)


@app.get("/api/sample", include_in_schema=False)
async def sample():
    """内置夏令时样例，可直接下载或在前端一键载入。"""
    return FileResponse(SAMPLE_PATH, media_type="text/csv; charset=utf-8",
                        filename="dst_sample.csv")


@app.post("/api/solve")
async def solve(file: UploadFile = File(...)):
    """上传 CSV，返回每个机尾的求解结果（唯一解/无解/多解/错误）。"""
    text = await _read_csv_text(file)
    try:
        return solve_csv(text)
    except CsvFormatError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/export")
async def export(file: UploadFile = File(...), tail: str = Form(...)):
    """导出机尾的 UTC CSV。仅当整链唯一解时允许；tail=__all__ 导出全部可导出机尾。"""
    text = await _read_csv_text(file)
    try:
        result = solve_csv(text)
    except CsvFormatError as exc:
        raise HTTPException(400, str(exc))

    if tail == "__all__":
        tails = [t["tail"] for t in result["tails"] if t["status"] == "unique"]
        if not tails:
            raise HTTPException(409, "没有可导出的机尾：仅整链唯一解的机尾允许导出")
        filename = "utc_export_all.csv"
    else:
        match = next((t for t in result["tails"] if t["tail"] == tail), None)
        if match is None:
            raise HTTPException(404, f"CSV 中不存在机尾 {tail!r}")
        if match["status"] != "unique":
            raise HTTPException(
                409, f"机尾 {tail} 状态为 {match['status']}，非整链唯一解，禁止导出")
        tails = [tail]
        filename = f"utc_export_{tail}.csv"

    csv_text = build_export_csv(result, tails)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/compare")
async def compare(file: UploadFile = File(...),
                  tz: str = Form(...),
                  tzif_before: UploadFile = File(...),
                  tzif_after: UploadFile = File(...)):
    """时区库升级对比：升级前/后两份 TZif 分别跑整链清洗并逐项比对。

    任一 TZif 无法读取时返回 400，detail 明确指出是升级前还是升级后文件，
    且不返回任何对比结果。普通 /api/solve 与 /api/export 完全不受影响。
    """
    text = await _read_csv_text(file)
    before_data = await _read_tzif_bytes(tzif_before)
    after_data = await _read_tzif_bytes(tzif_after)
    try:
        return compare_tzif_rules(text, tz, before_data, after_data)
    except CompareInputError as exc:
        raise HTTPException(400, str(exc))
    except CsvFormatError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/compare/sample/csv", include_in_schema=False)
async def compare_sample_csv():
    """内置时区升级对比样例 CSV：当地时间不变，入仓 UTC 因规则升级而改变。"""
    return FileResponse(COMPARE_SAMPLE_PATH, media_type="text/csv; charset=utf-8",
                        filename="tz_compare_sample.csv")


@app.get("/api/compare/sample/tzif/{which}", include_in_schema=False)
async def compare_sample_tzif(which: str):
    """内置样例 TZif：which 为 before（升级前）或 after（升级后）。"""
    if which not in ("before", "after"):
        raise HTTPException(404, "样例 TZif 只有 before / after 两份")
    return FileResponse(COMPARE_TZIF_DIR / f"mexico_{which}.tzif",
                        media_type="application/octet-stream",
                        filename=f"America_Mexico_City_{which}.tzif")


@app.get("/api/health", include_in_schema=False)
async def health():
    return {"status": "ok"}
