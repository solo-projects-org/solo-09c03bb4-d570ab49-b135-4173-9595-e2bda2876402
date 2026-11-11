"""时区库升级对比自检：直接运行 `python3 tests/test_compare.py`（仅标准库 + tzdata）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.compare import (  # noqa: E402
    CompareInputError, _chain_diff, compare_tzif_rules,
)
from app.solver import solve_csv  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "samples" / "tz_compare_sample.csv"
BEFORE = ROOT / "samples" / "tzif" / "mexico_before.tzif"
AFTER = ROOT / "samples" / "tzif" / "mexico_after.tzif"
TZ = "America/Mexico_City"


def check(name, cond):
    print(("PASS" if cond else "FAIL"), "-", name)
    if not cond:
        raise SystemExit(1)


def main():
    csv_text = CSV.read_text(encoding="utf-8")
    result = compare_tzif_rules(
        csv_text, TZ, BEFORE.read_bytes(), AFTER.read_bytes())

    check("样例识别出候选变化源行", result["summary"]["candidate_changed_rows"] == 5)
    check("样例识别出候选变化端点", result["summary"]["candidate_changed_ends"] == 7)
    check("样例受影响机尾为 B-8001/B-8002/B-8003",
          [t["tail"] for t in result["tails"]] == ["B-8001", "B-8002", "B-8003"])
    check("样例包含整链状态与可导出资格变化",
          result["summary"]["status_changed_tails"] == 1
          and result["summary"]["exportable_changed_tails"] == 1)
    # B-8001/B-8002 两边均为唯一解；B-8003 升级前多解、升级后唯一解
    by_tail = {t["tail"]: t for t in result["tails"]}
    check("B-8001/B-8002 两边唯一且可导出",
          all(by_tail[x]["before_status"] == "unique"
              and by_tail[x]["after_status"] == "unique"
              and by_tail[x]["before_exportable"]
              and by_tail[x]["after_exportable"] for x in ("B-8001", "B-8002")))
    check("B-8003 升级前多解禁止导出、升级后唯一可导出",
          by_tail["B-8003"]["before_status"] == "multiple"
          and not by_tail["B-8003"]["before_exportable"]
          and by_tail["B-8003"]["after_status"] == "unique"
          and by_tail["B-8003"]["after_exportable"])
    check("非唯一解一侧不输出整链最终 UTC 对比",
          by_tail["B-8003"]["chain_diff"]["available"] is False)

    # 当地时间本身没有变化，变化的只有 UTC 候选
    local_times = {
        (c["row"], c["end"]): c["local"] for c in result["candidate_changes"]}
    check("源行当地时刻保持当地时间格式",
          all("T" not in v and len(v) == 16 for v in local_times.values()))

    # 其他时区（America/New_York）照常使用系统 tzdata，不应被误报为候选变化
    check("非目标时区端点不进入候选变化列表",
          all(c["timezone"] == TZ for c in result["candidate_changes"]))

    # 两边唯一解时输出整链最终 UTC 变化
    by_tail = {t["tail"]: t for t in result["tails"]}
    b1 = by_tail["B-8001"]
    check("B-8001 两边唯一解可给出整链对比", b1["chain_diff"]["available"])
    check("B-8001 整链有 3 个最终 UTC 变化端点",
          len(b1["chain_diff"]["changes"]) == 3)
    check("B-8001 seq1 起飞 UTC 升级前后差 1 小时",
          b1["chain_diff"]["changes"][0]["before_utc"] == "2026-06-15T13:00:00Z"
          and b1["chain_diff"]["changes"][0]["after_utc"] == "2026-06-15T14:00:00Z")

    # 任一 TZif 无法读取 → 指明文件归属且不产出结果
    for bad_side, bad in (("升级前", b"TZif-broken"), ("升级后", b"not a tzif")):
        try:
            compare_tzif_rules(csv_text, TZ, bad if bad_side == "升级前"
                               else BEFORE.read_bytes(),
                               bad if bad_side == "升级后"
                               else AFTER.read_bytes())
        except CompareInputError as exc:
            check(f"{bad_side}坏文件会指出归属", bad_side in str(exc))
        else:
            check(f"{bad_side}坏文件应报错", False)

    # 两边唯一解时，候选未变但最终取值改变的端点也必须出现在整链对比中
    before_tail = {
        "segments": [
            {"seq": 1, "dep_local": "local-1", "dep_tz": TZ,
             "arr_local": "local-1b", "arr_tz": TZ},
            {"seq": 2, "dep_local": "local-2", "dep_tz": "America/New_York",
             "arr_local": "local-2b", "arr_tz": "America/New_York"},
        ],
        "solution": [
            {"seq": 1, "dep_utc": "A", "arr_utc": "B"},
            {"seq": 2, "dep_utc": "C", "arr_utc": "D"},
        ],
    }
    after_tail = {
        "segments": before_tail["segments"],
        "solution": [
            {"seq": 1, "dep_utc": "A2", "arr_utc": "B2"},
            {"seq": 2, "dep_utc": "C2", "arr_utc": "D"},
        ],
    }
    # 只有 seq1 的端点候选变化；seq2 起飞最终改变但候选未变
    changes = _chain_diff(before_tail, after_tail, "T-1",
                          {("T-1", 1, "dep"), ("T-1", 1, "arr")})
    seq2_dep = next(c for c in changes
                    if c["seq"] == 2 and c["end"] == "dep")
    check("候选未变的端点也列入整链最终 UTC 变化",
          seq2_dep["before_utc"] == "C" and seq2_dep["after_utc"] == "C2"
          and seq2_dep["candidate_changed"] is False)

    # 普通求解不受对比逻辑影响：仍按系统 tzdata 得到原结果
    plain = solve_csv(csv_text)
    check("普通求解仍按系统 tzdata 处理",
          {t["tail"]: t["status"] for t in plain["tails"]}
          == {"B-8001": "unique", "B-8002": "unique", "B-8003": "unique"})

    print("\n对比自检通过 ✅")


if __name__ == "__main__":
    main()
