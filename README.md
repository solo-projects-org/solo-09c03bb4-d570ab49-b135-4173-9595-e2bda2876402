# 航段时间清洗 · DST 安全入仓

面向航司数据工程师的网页工具：把地服导出的**当地时间**航段链解析为唯一 **UTC** 时间链，
避免夏令时（DST）切换时被错误入仓。

## 快速启动

```bash
docker compose up --build
```

浏览器打开 <http://localhost:8000> ，点击「📦 载入内置夏令时样例」→「开始清洗」即可看到完整演示。

「🕓 时区库升级对比」区域提供独立入口：点击「📦 载入时区升级样例」即可自动载入
一组**当地时间没改、入仓 UTC 却改变**的样例（CSV + 升级前/后两份 TZif），并直接展示对比结果。

本地开发（不用 Docker）：

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

自检（仅标准库）：`python3 tests/test_solver.py && python3 tests/test_compare.py`

## 输入 CSV 格式

UTF-8 编码，必需表头：

```csv
tail_number,seq,dep_local,dep_tz,arr_local,arr_tz,duration_min_minutes,duration_max_minutes
B-7001,1,2026-10-25 00:30,Europe/London,2026-10-25 02:15,Europe/Berlin,90,130
```

| 列 | 含义 |
| --- | --- |
| `tail_number` | 机尾号（同一机尾构成一条航段链） |
| `seq` | 航段顺序，整数，同一机尾内唯一，按升序链接 |
| `dep_local` / `arr_local` | 起飞/落地当地时间，`YYYY-MM-DD HH:MM`（也接受 `T` 分隔与秒） |
| `dep_tz` / `arr_tz` | 两端 IANA 时区名，如 `Europe/Berlin`、`America/New_York` |
| `duration_min_minutes` / `duration_max_minutes` | 飞行时长下/上限（分钟，**闭区间**） |

## 求解规则

1. 当地时间 → UTC 候选：
   - 普通时刻 → 1 个候选；
   - **重复时刻**（秋季拨回重叠，如柏林 2026-10-25 02:30）→ 保留 **2 个候选**参与整链求解；
   - **不存在的时刻**（春季拨快空洞，如柏林 2026-03-29 02:30）→ 该行报错，整架机尾禁止导出。
2. 每个航段的 (起飞, 落地) 候选组合必须满足：飞行时长 ∈ `[下限, 上限]`（闭区间）。
3. 按 `seq` 链接同一机尾：下一段起飞 **不得早于** 上一段落地（相等允许）。
4. 判定：
   - **整链唯一解** → 允许导出该机尾的 UTC CSV；
   - **无解** → 展示冲突航段（前向扫描定位第一个无法衔接的航段及其候选），禁止导出；
   - **多解** → 展示全部可行候选链（最多展示 20 条），禁止导出；
   - **行级错误**（不存在时刻 / 非法时区 / 格式错误 / seq 重复等）→ 禁止导出。

## 导出

- 网页上按机尾导出，或「导出全部可导出机尾」；
- 导出列：`tail_number,seq,dep_utc,arr_utc,duration_minutes`（UTC 为 ISO 8601 `Z` 格式）；
- 服务端在导出时会**重新求解并复核唯一性**，非唯一解返回 409，杜绝误导出。

## 时区库升级对比

用于升级 tzdata/时区库前评估影响：上传地服原始 CSV、一个 IANA 时区名，以及该时区
**升级前 / 升级后**两份 TZif 文件。

- 两套规则分别跑完整清洗链；**只有指定时区使用上传的 TZif**，CSV 里其他时区仍按系统 tzdata 处理；
- 普通「开始清洗」与两个导出按钮不经过对比逻辑，结果不受影响；
- 网页列出 UTC 候选发生变化的源行、起降端及升级前/后候选；
- 对这些源行所属机尾，对照升级前/后的整链状态（唯一解/无解/多解/错误）与可导出资格；
- 当某机尾在两边均为唯一解时，列出整条链上所有最终起降 UTC 的变化，包含**候选未变、但因整链约束最终取值改变**的端点；
- 任一 TZif 无法读取时，接口返回 400 并明确说明是「升级前」还是「升级后」文件有误，不返回任何对比结果。

内置对比样例 `samples/tz_compare_sample.csv` + `samples/tzif/mexico_before.tzif` /
`mexico_after.tzif` 模拟墨西哥城 2026 年取消夏令时：当地时间完全不变，UTC 候选与最终入仓时刻整体偏移 1 小时。
其中 B-8003 在升级前因重复时刻多解、升级后变为唯一解，用于演示整链状态与可导出资格的变化。
TZif 对应的 `zic` 源文件也保留在 `samples/tzif/` 便于复核。

## 内置样例（samples/dst_sample.csv）

| 机尾 | 场景 | 期望结果 |
| --- | --- | --- |
| B-7001 | 欧洲拨回日三连段，重复时刻由时长区间+链约束消歧 | 唯一解，可导出 |
| B-7002 | 柏林 2026-03-29 02:30 不存在（拨快空洞） | 数据错误 |
| B-7003 | 第二段起飞早于第一段落地 | 无解，展示冲突航段 |
| B-7004 | 起降均为重复时刻，区间宽松 | 多解（2 条候选链） |
| B-7005 | 美国拨回日（纽约/洛杉矶/丹佛） | 唯一解，可导出 |

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 网页入口 |
| `GET` | `/api/sample` | 下载内置样例 CSV |
| `POST` | `/api/solve` | 上传 CSV（multipart 字段 `file`），返回逐机尾求解结果 JSON |
| `POST` | `/api/export` | 字段 `file` + `tail`（`__all__` 表示全部可导出机尾），返回 UTC CSV；非唯一解 409 |
| `POST` | `/api/compare` | 字段 `file`、`tz`、`tzif_before`、`tzif_after`；对比两套 TZif 规则的整链清洗结果；TZif 有误 400 |
| `GET` | `/api/compare/sample/csv` | 内置时区升级对比样例 CSV |
| `GET` | `/api/compare/sample/tzif/{before\|after}` | 内置样例的升级前/后 TZif |
| `GET` | `/docs` | FastAPI 自动文档 |

## 目录结构

```
app/
  main.py            # FastAPI 路由
  solver.py          # 候选生成 + 整链求解 + 冲突定位（纯标准库）
  compare.py         # 时区库升级对比（TZif 校验 + 两套规则求解 + 逐项比对）
  static/index.html  # 单页前端
samples/dst_sample.csv
samples/tz_compare_sample.csv
samples/tzif/        # 对比样例的升级前/后 TZif 与 zic 源文件
tests/test_solver.py
tests/test_compare.py
Dockerfile / docker-compose.yml / requirements.txt
```
