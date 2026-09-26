"""verify 服务：代码测试 + 构建检查 + API 冒烟，完成后退出并返回退出码。

覆盖场景：唯一故障、多解裁决、不可行校验、可定位拒绝、健康检查与复核回查。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
FAILURES: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" —— {extra}" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


def build_checks() -> None:
    print("== 构建检查 ==")
    r = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "app", "verify", "tests"], cwd=ROOT
    )
    check("字节码编译 app/verify/tests", r.returncode == 0)
    try:
        import app.main  # noqa: F401
        import app.solver  # noqa: F401
        import app.store  # noqa: F401
        check("关键模块可导入", True)
    except Exception as exc:  # pragma: no cover
        check("关键模块可导入", False, repr(exc))
    html_path = ROOT / "app" / "static" / "index.html"
    ok = html_path.exists() and html_path.stat().st_size > 0
    check("静态页面存在且非空", ok)
    if ok:
        html = html_path.read_text(encoding="utf-8")
        for token in ['id="channel-input"', 'id="checks"', 'id="result-panel"',
                      "复核编号", "逐校验复算"]:
            check(f"页面包含关键元素 {token}", token in html)
    # 求解器已知结论自洽
    from app.solver import solve_min_weight
    r1 = solve_min_weight(["1", "2", "3"], [
        {"channels": ["1", "2"], "parity": 1},
        {"channels": ["2", "3"], "parity": 1},
        {"channels": ["1", "3"], "parity": 0},
    ])
    check("求解器自洽（唯一故障 → ['2']）",
          r1.feasible and r1.fault_channels == ["2"] and r1.weight == 1)


def unit_tests() -> None:
    print("== 代码测试（pytest） ==")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(ROOT / "tests")], cwd=ROOT
    )
    check("pytest 全部通过", r.returncode == 0)


def http(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode() or "null")
        except Exception:
            payload = None
        return e.code, payload


def locs_of(body) -> list[tuple]:
    if isinstance(body, dict) and isinstance(body.get("detail"), list):
        return [tuple(e.get("loc", [])) for e in body["detail"]]
    return []


def api_smoke() -> None:
    print("== API 冒烟 ==")
    for _ in range(60):
        try:
            s, _ = http("GET", "/healthz")
            if s == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    s, b = http("GET", "/healthz")
    check("GET /healthz → 200", s == 200 and isinstance(b, dict) and b.get("status") == "ok")

    # 场景一：唯一故障
    s, b = http("POST", "/api/reviews", {
        "channels": ["1", "2", "3"],
        "checks": [
            {"channels": ["1", "2"], "parity": 1},
            {"channels": ["2", "3"], "parity": 1},
            {"channels": ["1", "3"], "parity": 0}]})
    ok = (s == 201 and isinstance(b, dict)
          and b["result"]["status"] == "optimal"
          and b["result"]["fault_channels"] == ["2"]
          and b["result"]["weight"] == 1)
    check("唯一故障：POST 201 且故障通道为 ['2']", ok, f"got {s} {b}")
    rid = b.get("review_id") if isinstance(b, dict) else None
    check("返回复核编号", isinstance(rid, str) and rid.startswith("R-"))
    if rid:
        s2, b2 = http("GET", f"/api/reviews/{rid}")
        check("复核编号可回查且逐校验复算一致",
              s2 == 200 and all(c["ok"] for c in b2["result"]["checks"]),
              f"got {s2} {b2}")

    # 场景二：多解裁决（同重取升序选择向量字典序最小者）
    s, b = http("POST", "/api/reviews", {
        "channels": ["1", "2", "3", "4"],
        "checks": [
            {"channels": ["1", "2"], "parity": 1},
            {"channels": ["3", "4"], "parity": 1}]})
    ok = (s == 201 and isinstance(b, dict)
          and b["result"]["fault_channels"] == ["2", "4"]
          and b["result"]["selection_vector"] == "0101"
          and b["result"]["weight"] == 2)
    check("多解裁决：同重候选中取 ['2','4']（选择向量 0101）", ok, f"got {s} {b}")

    # 场景三：不可行校验
    s, b = http("POST", "/api/reviews", {
        "channels": ["1", "2", "3"],
        "checks": [
            {"channels": ["1", "2"], "parity": 0},
            {"channels": ["1", "3"], "parity": 0},
            {"channels": ["2", "3"], "parity": 1}]})
    ok = (s == 201 and isinstance(b, dict)
          and b["result"]["status"] == "infeasible"
          and b["result"]["fault_channels"] is None)
    check("不可行：保存不可行结论而非近似集合", ok, f"got {s} {b}")
    rid3 = b.get("review_id") if isinstance(b, dict) else None
    if rid3:
        s2, b2 = http("GET", f"/api/reviews/{rid3}")
        check("不可行结论已持久化可回查",
              s2 == 200 and b2["result"]["status"] == "infeasible", f"got {s2} {b2}")

    # 场景四：非法输入的可定位拒绝
    s, b = http("POST", "/api/reviews", {
        "channels": ["1", "1"],
        "checks": [{"channels": ["1"], "parity": 1}]})
    check("重复通道 → 422 且 loc 可定位到 channels[1]",
          s == 422 and ("channels", 1) in locs_of(b), f"got {s} {b}")
    s, b = http("POST", "/api/reviews", {
        "channels": ["1", "2"],
        "checks": [
            {"channels": ["1", "2"], "parity": 1},
            {"channels": ["2", "1"], "parity": 0}]})
    check("重复校验集合 → 422 且 loc 指向 checks[1].channels",
          s == 422 and ("checks", 1, "channels") in locs_of(b), f"got {s} {b}")
    s, b = http("POST", "/api/reviews", {
        "channels": ["1", "2"],
        "checks": [{"channels": [], "parity": 1}]})
    check("空校验集合 → 422 且 loc 指向 checks[0].channels",
          s == 422 and ("checks", 0, "channels") in locs_of(b), f"got {s} {b}")
    s, b = http("GET", "/api/reviews/R-doesnotexist")
    check("未知复核编号 → 404", s == 404)


def main() -> int:
    print(f"API_BASE={API}")
    build_checks()
    unit_tests()
    api_smoke()
    print("\n== 汇总 ==")
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
