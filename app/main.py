"""硅像素读出板噪声故障定位 —— Web 页面与复核接口。"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import store
from .solver import natural_key, solve_min_weight

BASE_DIR = Path(__file__).resolve().parent
INDEX_HTML = BASE_DIR / "static" / "index.html"

MIN_CHANNELS = 2
MAX_CHANNELS = 36
MAX_CHECKS = 28
CHANNEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,15}$")


def _err(loc, msg):
    """可定位的拒绝信息：loc 指向出错字段（如 ["checks", 1, "channels", 2]）。"""
    return {"loc": list(loc), "msg": msg}


def _norm_channel(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def validate_payload(body):
    """校验并规范化请求体，返回 (payload, errors)；errors 为空方可求解。"""
    if not isinstance(body, dict):
        return None, [_err(["body"], "请求体必须是 JSON 对象")]
    errors = []

    # ---- 通道清单：2–36 个唯一通道 ----
    raw_channels = body.get("channels")
    channels: list[str] = []
    channels_ok = isinstance(raw_channels, list)
    if not channels_ok:
        errors.append(_err(["channels"], "channels 必须是通道标识数组"))
    else:
        seen: dict[str, int] = {}
        for i, item in enumerate(raw_channels):
            cid = _norm_channel(item)
            if cid is None:
                errors.append(_err(["channels", i], "通道标识必须是非空字符串（或整数）"))
                continue
            if not CHANNEL_RE.fullmatch(cid):
                errors.append(_err(
                    ["channels", i],
                    f"通道标识 '{cid}' 非法：仅允许字母、数字、'_'、'-'，"
                    "须以字母或数字开头，最长 16 字符",
                ))
                continue
            if cid in seen:
                errors.append(_err(
                    ["channels", i],
                    f"通道标识 '{cid}' 与第 {seen[cid] + 1} 个通道重复",
                ))
                continue
            seen[cid] = i
            channels.append(cid)
        if len(channels) < MIN_CHANNELS:
            errors.append(_err(
                ["channels"],
                f"唯一通道数量不足：至少 {MIN_CHANNELS} 个（当前 {len(channels)} 个）",
            ))
        elif len(channels) > MAX_CHANNELS:
            errors.append(_err(
                ["channels"],
                f"唯一通道数量超限：至多 {MAX_CHANNELS} 个（当前 {len(channels)} 个）",
            ))
    declared = set(channels) if channels_ok else None

    # ---- 校验列表：1–28 条，每条引用的通道集合非空且互不重复 ----
    raw_checks = body.get("checks")
    checks: list[dict] = []
    if not isinstance(raw_checks, list) or not raw_checks:
        errors.append(_err(["checks"], f"checks 必须是非空数组（1–{MAX_CHECKS} 条校验）"))
    else:
        if len(raw_checks) > MAX_CHECKS:
            errors.append(_err(
                ["checks"],
                f"校验条数超限：至多 {MAX_CHECKS} 条（当前 {len(raw_checks)} 条）",
            ))
        seen_sets: dict[frozenset, int] = {}
        for i, raw in enumerate(raw_checks):
            if not isinstance(raw, dict):
                errors.append(_err(
                    ["checks", i], "每条校验必须是对象：{channels: [...], parity: 0|1}"
                ))
                continue
            norm: list[str] = []
            raw_set = raw.get("channels")
            if not isinstance(raw_set, list) or not raw_set:
                errors.append(_err(["checks", i, "channels"], "校验引用的通道集合不能为空"))
            else:
                local: set[str] = set()
                for k, item in enumerate(raw_set):
                    cid = _norm_channel(item)
                    if cid is None:
                        errors.append(_err(
                            ["checks", i, "channels", k],
                            "通道标识必须是非空字符串（或整数）",
                        ))
                        continue
                    if cid in local:
                        errors.append(_err(
                            ["checks", i, "channels", k],
                            f"通道 '{cid}' 在本校验内重复",
                        ))
                        continue
                    if declared is not None and cid not in declared:
                        errors.append(_err(
                            ["checks", i, "channels", k],
                            f"通道 '{cid}' 未在通道清单中声明",
                        ))
                        continue
                    local.add(cid)
                    norm.append(cid)
                if norm:
                    key = frozenset(norm)
                    if key in seen_sets:
                        errors.append(_err(
                            ["checks", i, "channels"],
                            f"与第 {seen_sets[key] + 1} 条校验引用的通道集合重复",
                        ))
                    else:
                        seen_sets[key] = i
            parity = raw.get("parity")
            value = None
            if isinstance(parity, bool):
                value = int(parity)
            elif isinstance(parity, int) and parity in (0, 1):
                value = parity
            elif isinstance(parity, str) and parity.strip() in ("0", "1"):
                value = int(parity.strip())
            if value is None:
                errors.append(_err(["checks", i, "parity"], "观测奇偶值必须为 0 或 1"))
            if norm and value is not None:
                checks.append({"channels": norm, "parity": value})

    if errors:
        return None, errors
    return {"channels": channels, "checks": checks}, []


def build_result(payload: dict) -> dict:
    """调用折半求解器并整理复核结论；不可行时给出不可行结论而非近似集合。"""
    solved = solve_min_weight(payload["channels"], payload["checks"])
    order = sorted(payload["channels"], key=natural_key)
    pos = {c: i for i, c in enumerate(order)}

    checks_out = []
    for i, chk in enumerate(payload["checks"]):
        recomputed = None
        ok = None
        if solved.feasible:
            acc = 0
            for c in chk["channels"]:
                acc ^= solved.selection[pos[c]]
            recomputed = acc
            ok = acc == chk["parity"]
        checks_out.append({
            "index": i,
            "channels": chk["channels"],
            "observed": chk["parity"],
            "recomputed": recomputed,
            "ok": ok,
        })

    if not solved.feasible:
        return {
            "status": "infeasible",
            "weight": None,
            "selection_vector": None,
            "fault_channels": None,
            "ordered_channels": order,
            "checks": checks_out,
        }
    return {
        "status": "optimal",
        "weight": solved.weight,
        "selection_vector": "".join(str(b) for b in solved.selection),
        "fault_channels": solved.fault_channels,
        "ordered_channels": order,
        "checks": checks_out,
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.init_db()
    yield


app = FastAPI(title="硅像素读出板噪声故障定位", version="1.0.0", lifespan=lifespan)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(INDEX_HTML)


@app.post("/api/reviews", status_code=201)
def create_review(body: dict = Body(...)):
    payload, errors = validate_payload(body)
    if errors:
        # 输入非法 / 重复通道 / 集合重复等：可定位的拒绝信息
        raise HTTPException(status_code=422, detail=errors)
    result = build_result(payload)
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input": payload,
        "result": result,
    }
    record["review_id"] = store.create_review(record)
    return record


@app.get("/api/reviews/{review_id}")
def get_review(review_id: str):
    record = store.get_review(review_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=[{"loc": ["review_id"], "msg": f"未找到复核编号 '{review_id}'"}],
        )
    return record
