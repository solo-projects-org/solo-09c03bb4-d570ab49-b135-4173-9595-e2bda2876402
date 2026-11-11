"""时区库升级对比：用升级前/后的 TZif 规则分别跑整链清洗，再逐项比对。

约束
----
- 只替换用户指定的那一个 IANA 时区；CSV 中其他时区仍使用系统 tzdata；
- 普通清洗（/api/solve）与导出（/api/export）不经过本模块，因此不受影响；
- 任一 TZif 无法读取时立即报错，不产出任何对比结果，并明确指出是升级前还是升级后文件。
"""
from __future__ import annotations

import io
from datetime import datetime
from zoneinfo import ZoneInfo

from .solver import (
    iso,
    parse_naive,
    read_rows,
    solve_csv,
    utc_candidates,
)

_BEFORE_LABEL = "升级前"
_AFTER_LABEL = "升级后"


class CompareInputError(Exception):
    """对比输入有误（TZif 无法读取、时区名为空等）。"""


def load_tzif(data: bytes, tz_name: str, label: str) -> ZoneInfo:
    """把上传的 TZif 内容载入为 ZoneInfo；失败时抛出带文件归属的错误。"""
    if not data:
        raise CompareInputError(f"{label} TZif 文件无法读取：文件为空")
    if data[:4] != b"TZif":
        raise CompareInputError(
            f"{label} TZif 文件无法读取：不是有效的 TZif 格式（缺少 TZif 文件头）")
    try:
        return ZoneInfo.from_file(io.BytesIO(data), key=tz_name)
    except Exception as exc:  # ZoneInfo.from_file 可能抛出 ValueError/OSError 等
        raise CompareInputError(
            f"{label} TZif 文件无法读取：{exc}") from exc


def _candidate_state(naive: datetime, tz_name: str, overrides) -> dict:
    """单个端点在给定规则下的候选状态。"""
    candidates, error = utc_candidates(naive, tz_name, overrides)
    return {
        "candidates": [iso(c) for c in (candidates or [])],
        "error": error,
    }


def _same_state(a: dict, b: dict) -> bool:
    return a["candidates"] == b["candidates"] and a["error"] == b["error"]


def _seq_value(raw) -> object:
    try:
        return int(raw.seq_raw)
    except (TypeError, ValueError):
        return raw.seq_raw


def _find_candidate_changes(raws, tz_name: str, before_zone, after_zone) -> list:
    """逐源行、逐起降端比较升级前后的 UTC 候选。"""
    before_overrides = {tz_name: before_zone}
    after_overrides = {tz_name: after_zone}
    changes = []

    for raw in raws:
        for end, local_raw, tz_raw in (
            ("dep", raw.dep_local_raw, raw.dep_tz),
            ("arr", raw.arr_local_raw, raw.arr_tz),
        ):
            naive = parse_naive(local_raw)
            if naive is None:
                # 当地时间本身无法解析，属于行级错误；两套规则都一样，不算候选变化
                continue
            before = _candidate_state(naive, tz_raw, before_overrides)
            after = _candidate_state(naive, tz_raw, after_overrides)
            if _same_state(before, after):
                continue
            changes.append({
                "row": raw.row_number,
                "tail": raw.tail,
                "seq": _seq_value(raw),
                "end": end,
                "local": local_raw,
                "timezone": tz_raw,
                "before": before,
                "after": after,
                # 平铺字段，便于接口调用方直接读取前后候选
                "before_candidates": before["candidates"],
                "after_candidates": after["candidates"],
                "before_error": before["error"],
                "after_error": after["error"],
            })
    return changes


def _chain_diff(before_tail: dict, after_tail: dict, tail: str,
                candidate_keys: set):
    """两边都唯一解时，逐航段比较最终起降 UTC。

    不仅比较候选发生变化的端点：整链选择可能因上游/下游约束改变，
    因此即使某端点候选未变，最终选中的 UTC 也可能变化。
    """
    before_segments = {s["seq"]: s for s in before_tail.get("segments", [])}
    before_solution = {s["seq"]: s for s in before_tail.get("solution", [])}
    after_solution = {s["seq"]: s for s in after_tail.get("solution", [])}

    changes = []
    for seq in sorted(before_solution):
        b = before_solution[seq]
        a = after_solution.get(seq)
        if a is None:
            continue
        seg = before_segments.get(seq, {})
        for end in ("dep", "arr"):
            before_utc = b.get(f"{end}_utc")
            after_utc = a.get(f"{end}_utc")
            if before_utc == after_utc:
                continue
            tz_key = "dep_tz" if end == "dep" else "arr_tz"
            local_key = "dep_local" if end == "dep" else "arr_local"
            changes.append({
                "seq": seq,
                "end": end,
                "local": seg.get(local_key, ""),
                "timezone": seg.get(tz_key, ""),
                "before_utc": before_utc,
                "after_utc": after_utc,
                "candidate_changed": (tail, seq, end) in candidate_keys,
            })
    return changes


def compare_tzif_rules(csv_text: str, tz_name: str,
                       before_data: bytes, after_data: bytes) -> dict:
    """对比两套 TZif 规则下的整链清洗结果。

    TZif 在求解前全部校验；任一文件有误都会抛出 CompareInputError，
    调用方据此返回错误，不展示对比结果。
    """
    tz_name = (tz_name or "").strip()
    if not tz_name:
        raise CompareInputError("请填写 IANA 时区名（例如 America/Mexico_City）")
    if any(ch.isspace() for ch in tz_name):
        raise CompareInputError(f"IANA 时区名不能包含空白字符: {tz_name!r}")

    # 先校验两份规则文件，任何一份坏掉都不继续对比
    failures = []
    before_zone = after_zone = None
    try:
        before_zone = load_tzif(before_data, tz_name, _BEFORE_LABEL)
    except CompareInputError as exc:
        failures.append(str(exc))
    try:
        after_zone = load_tzif(after_data, tz_name, _AFTER_LABEL)
    except CompareInputError as exc:
        failures.append(str(exc))
    if failures:
        raise CompareInputError("；".join(failures))

    raws = read_rows(csv_text)

    candidate_changes = _find_candidate_changes(
        raws, tz_name, before_zone, after_zone)
    before_result = solve_csv(csv_text, {tz_name: before_zone})
    after_result = solve_csv(csv_text, {tz_name: after_zone})

    before_tails = {t["tail"]: t for t in before_result["tails"]}
    after_tails = {t["tail"]: t for t in after_result["tails"]}

    # 受影响的机尾：候选出现变化的源行所属机尾
    affected = []
    seen = set()
    for change in candidate_changes:
        tail = change["tail"]
        if tail not in seen:
            seen.add(tail)
            affected.append(tail)

    candidate_keys = {(c["tail"], c["seq"], c["end"])
                      for c in candidate_changes}
    tail_views = []
    status_changed = export_changed = chain_changed = 0
    for tail in affected:
        bt = before_tails[tail]
        at = after_tails[tail]
        before_status = bt["status"]
        after_status = at["status"]
        before_exportable = before_status == "unique"
        after_exportable = after_status == "unique"
        if before_status != after_status:
            status_changed += 1
        if before_exportable != after_exportable:
            export_changed += 1

        if before_exportable and after_exportable:
            changes = _chain_diff(bt, at, tail, candidate_keys)
            chain_diff = {"available": True, "reason": None, "changes": changes}
            if changes:
                chain_changed += 1
        else:
            if before_exportable or after_exportable:
                reason = "至少一侧不是整链唯一解，无法给出整条链的最终 UTC 对比"
            else:
                reason = "两侧都不是整链唯一解，无法给出整条链的最终 UTC 对比"
            chain_diff = {"available": False, "reason": reason, "changes": []}

        tail_views.append({
            "tail": tail,
            "candidate_change_count": sum(
                1 for c in candidate_changes if c["tail"] == tail),
            "before_status": before_status,
            "after_status": after_status,
            "before_exportable": before_exportable,
            "after_exportable": after_exportable,
            "status_changed": before_status != after_status,
            "exportable_changed": before_exportable != after_exportable,
            "chain_diff": chain_diff,
            "chain_changes": chain_diff["changes"],
        })

    changed_rows = {c["row"] for c in candidate_changes}
    return {
        "timezone": tz_name,
        "summary": {
            "rows": len(raws),
            "total_tails": len(before_result["tails"]),
            "candidate_changed_rows": len(changed_rows),
            "candidate_changed_ends": len(candidate_changes),
            "affected_tails": len(affected),
            "status_changed_tails": status_changed,
            "exportable_changed_tails": export_changed,
            "chain_changed_tails": chain_changed,
        },
        "candidate_changes": candidate_changes,
        "tails": tail_views,
    }
