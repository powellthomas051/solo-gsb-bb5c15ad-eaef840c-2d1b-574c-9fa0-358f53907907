"""管段扩容预览 API 端到端测试: FastAPI TestClient + 临时 SQLite。

重点验证: 独立 API 可用、原容量足够(零费用空清单)/扩容后满足/全部候选
仍不足三类结果、费用与字典序决胜、候选录入 422、以及**只读**——不修改
当前管网、不写入历史方案、不改变原方案。
"""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")

import sys  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
from demo import DEMO_NETWORK  # noqa: E402

_client_cm = TestClient(main.app)
client = _client_cm.__enter__()  # 进入生命周期: lifespan 建表

ok = 0


def check(cond, msg):
    global ok
    assert cond, "FAIL: " + msg
    ok += 1


# 健康检查
r = client.get("/api/health")
check(r.status_code == 200, "health")

# ---- 示例管网常规方案: 关 V2+V4, 未关阀管段 s1..s4, 必保点 H1,H2 ----
r = client.post("/api/plan", json=DEMO_NETWORK)
check(r.status_code == 200, f"plan {r.status_code}")
plan = r.json()
check(plan["feasible"] and plan["closed_valves"] == ["V2", "V4"], "plan saved")
pid = plan["id"]
before = client.get(f"/api/plans/{pid}").json()
plans_before = client.get("/api/plans?limit=100").json()["plans"]
net_before = client.get("/api/network").json()

PATH = f"/api/plans/{pid}/capacity-expansion"
CAPS5 = {"s1": 5, "s2": 3, "s3": 10, "s4": 4}
SUP = {"S": 100}
DEM = {"H1": 3, "H2": 4}

# ---- 原容量已足够: 零费用空清单(即便带了候选) ----
r = client.post(PATH, json={
    "capacities": {"s1": 10, "s2": 3, "s3": 10, "s4": 4},
    "supplies": SUP, "demands": DEM,
    "expansions": [{"segment_id": "s1", "increment": 1, "cost": 1}],
})
check(r.status_code == 200, f"baseline sufficient status {r.status_code} {r.text}")
res = r.json()
check(res["ok"] and res["sufficient"] and res["satisfied"]
      and not res["empty"], "baseline flags")
check(res["plan_id"] == pid and res["solver"] == "base", "meta fields")
check(res["selected_segment_ids"] == [] and res["selected_expansions"] == []
      and res["total_cost"] == 0, "zero-cost empty list")
check(res["total_demand"] == 7 and res["delivered_total"] == 7
      and res["shortage_total"] == 0, "baseline totals")
check(res["candidate_count"] == 1 and res["combination_count"] == 2
      and res["feasible_combination_count"] == 2, "combo stats")
check("无需扩容" in res["message"] and "只读" in res["message"], "message")

# ---- 只读: 原方案、历史列表、当前管网均未改变 ----
check(client.get(f"/api/plans/{pid}").json() == before, "plan unchanged")
check(client.get("/api/plans?limit=100").json()["plans"] == plans_before,
      "no plan saved by expansion preview")
check(client.get("/api/network").json() == net_before, "network untouched")

# ---- 容量不足, 扩容可行: s1+2 费10 可行, s4+2 费3 单独不可行 ----
r = client.post(PATH, json={
    "capacities": CAPS5, "supplies": SUP, "demands": DEM,
    "expansions": [
        {"segment_id": "s1", "increment": 2, "cost": 10},
        {"segment_id": "s4", "increment": 2, "cost": 3},
    ],
})
check(r.status_code == 200, f"expansion status {r.status_code} {r.text}")
res = r.json()
check(res["ok"] and res["satisfied"] and not res["empty"], "expansion flags")
check(res["selected_segment_ids"] == ["s1"], f"selected {res['selected_segment_ids']}")
check(res["total_cost"] == 10, "expansion total cost")
check(res["delivered_total"] == 7 and res["shortage_total"] == 0
      and res["total_demand"] == 7, "expanded totals")
check(res["baseline_delivered_total"] == 5, "baseline 5")
check(res["combination_count"] == 4
      and res["feasible_combination_count"] == 2, "4 combos / 2 feasible")
sel = res["selected_expansions"][0]
check(sel["segment_id"] == "s1" and sel["capacity"] == 5
      and sel["increment"] == 2 and sel["expanded_capacity"] == 7
      and sel["cost"] == 10, f"selected row {sel}")
check(res["effective_capacities"] == {"s1": 7, "s2": 3, "s3": 10, "s4": 4},
      "effective capacities")
summary = {ns["node_id"]: (ns["delivered"], ns["shortage"])
           for ns in res["node_summary"]}
check(summary == {"H1": (3, 0), "H2": (4, 0)}, f"node summary {summary}")
# 候选回显按管段编号升序, 含扩容后上限
check([c["segment_id"] for c in res["candidates"]] == ["s1", "s4"]
      and res["candidates"][1]["expanded_capacity"] == 6, "candidate echo")
check(res["all_candidates_cost_total"] == 13, "all candidate cost")
# 生效上限行同时带原上限与扩容后上限
row = {x["id"]: x for x in res["open_segments"]}["s1"]
check(row["capacity"] == 5 and row["effective_capacity"] == 7, "open segment row")

# ---- 同费字典序决胜: 双支路网络, 扩 a / 扩 c 同效同费 -> 取 a ----
NET_TIE = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "X", "role": "normal"},
        {"id": "Y", "role": "normal"}, {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "a", "endpoint_a": "S", "endpoint_b": "X"},
        {"id": "b", "endpoint_a": "X", "endpoint_b": "H"},
        {"id": "c", "endpoint_a": "S", "endpoint_b": "Y"},
        {"id": "d", "endpoint_a": "Y", "endpoint_b": "H"},
    ],
    "valves": [],
}
r = client.post("/api/plan", json=NET_TIE)
tie_pid = r.json()["id"]
r = client.post(f"/api/plans/{tie_pid}/capacity-expansion", json={
    "capacities": {"a": 3, "b": 10, "c": 3, "d": 10},
    "supplies": {"S": 100}, "demands": {"H": 8},
    "expansions": [
        {"segment_id": "c", "increment": 4, "cost": 5},
        {"segment_id": "a", "increment": 4, "cost": 5},
    ],
})
check(r.status_code == 200, f"tie status {r.status_code} {r.text}")
res = r.json()
check(res["selected_segment_ids"] == ["a"] and res["total_cost"] == 5,
      f"lex tie {res['selected_segment_ids']}")
check(res["feasible_combination_count"] == 3, "3 feasible combos")

# 费用不同: c 便宜 -> 选 c
r = client.post(f"/api/plans/{tie_pid}/capacity-expansion", json={
    "capacities": {"a": 3, "b": 10, "c": 3, "d": 10},
    "supplies": {"S": 100}, "demands": {"H": 8},
    "expansions": [
        {"segment_id": "a", "increment": 4, "cost": 5},
        {"segment_id": "c", "increment": 4, "cost": 1},
    ],
})
res = r.json()
check(res["selected_segment_ids"] == ["c"] and res["total_cost"] == 1,
      "cost beats lex")

# ---- 全部候选仍不足: 全选后最大送达量与总缺口, total_cost=null ----
r = client.post(f"/api/plans/{tie_pid}/capacity-expansion", json={
    "capacities": {"a": 3, "b": 4, "c": 3, "d": 4},
    "supplies": {"S": 100}, "demands": {"H": 9},
    "expansions": [
        {"segment_id": "a", "increment": 1, "cost": 1},
        {"segment_id": "c", "increment": 1, "cost": 1},
    ],
})
check(r.status_code == 200, f"insufficient status {r.status_code}")
res = r.json()
check(not res["ok"] and res["reason_code"] == "INSUFFICIENT_EXPANSION"
      and not res["satisfied"], "insufficient flags")
check(res["delivered_total"] == 8 and res["shortage_total"] == 1
      and res["total_demand"] == 9, "insufficient totals")
check(res["total_cost"] is None and res["selected_segment_ids"] == []
      and res["selected_expansions"] == [], "no expansion plan")
check(res["feasible_combination_count"] == 0, "zero feasible")
check(res["effective_capacities"] == {"a": 4, "b": 4, "c": 4, "d": 4},
      "all-selected effective caps")
check(res["node_summary"][0]["shortage"] == 1, "node shortage")
check("总缺口" in res["message"], "insufficient message")

# ---- 无候选且容量不足: 仍返回 200 + 不足结论 ----
r = client.post(PATH, json={
    "capacities": CAPS5, "supplies": SUP, "demands": DEM, "expansions": [],
})
res = r.json()
check(not res["ok"] and res["delivered_total"] == 5
      and res["shortage_total"] == 2 and res["total_cost"] is None
      and res["combination_count"] == 1, "no candidate insufficient")

# ---- 方案不存在 ----
r = client.post("/api/plans/9999/capacity-expansion", json={
    "capacities": CAPS5, "supplies": SUP, "demands": DEM})
check(r.status_code == 404, f"404 got {r.status_code}")

# ---- 不可行方案: 不能做扩容预览 ----
BAD = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "L"},
        {"id": "p2", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [],
}
r = client.post("/api/plan", json=BAD)
bad_id = r.json()["id"]
r = client.post(f"/api/plans/{bad_id}/capacity-expansion", json={
    "capacities": {"p1": 1, "p2": 1}, "supplies": {"S": 1},
    "demands": {"H": 1}})
check(r.status_code == 422, f"infeasible expansion {r.status_code}")
errs = r.json()["detail"]["errors"]
check(any("不可行" in e and str(bad_id) in e for e in errs),
      f"infeasible msg {errs}")


# ---- 录入校验: 422 + detail.errors ----
def expect_422(body, keys):
    r = client.post(PATH, json=body)
    check(r.status_code == 422, f"422 for {keys}: got {r.status_code} {r.text}")
    errs = r.json()["detail"]["errors"]
    joined = " | ".join(errs)
    for key in keys:
        check(key in joined, f"error contains {key!r}; got: {errs}")


# 超过 6 个候选
expect_422({"capacities": CAPS5, "supplies": SUP, "demands": DEM,
            "expansions": [{"segment_id": "s1", "increment": 1, "cost": 1}] * 7},
           ["扩容候选数量为 7, 超过上限 6 个"])
# 扩容已关阀管段 / 不存在管段
expect_422({"capacities": CAPS5, "supplies": SUP, "demands": DEM,
            "expansions": [{"segment_id": "s5", "increment": 1, "cost": 1}]},
           ["扩容管段 s5 不是方案快照中未关阀的管段"])
expect_422({"capacities": CAPS5, "supplies": SUP, "demands": DEM,
            "expansions": [{"segment_id": "zz", "increment": 1, "cost": 1}]},
           ["扩容管段 zz 不是方案快照中未关阀的管段"])
# 同一管段多个候选
expect_422({"capacities": CAPS5, "supplies": SUP, "demands": DEM,
            "expansions": [
                {"segment_id": "s1", "increment": 1, "cost": 1},
                {"segment_id": "s1", "increment": 2, "cost": 2}]},
           ["管段 s1 出现了多个扩容候选"])
# 缺管段
expect_422({"capacities": CAPS5, "supplies": SUP, "demands": DEM,
            "expansions": [{"increment": 1, "cost": 1}]},
           ["扩容候选 #1: 必须选择扩容管段"])
# 增量 / 费用非正整数
for bad in [0, -1, 1.5, "2", True, None]:
    expect_422({"capacities": CAPS5, "supplies": SUP, "demands": DEM,
                "expansions": [{"segment_id": "s1", "increment": bad,
                                "cost": 1}]},
               ["扩容候选 s1: 增量必须是正整数"])
for bad in [0, -2, 3.5, "1", False, None]:
    expect_422({"capacities": CAPS5, "supplies": SUP, "demands": DEM,
                "expansions": [{"segment_id": "s1", "increment": 1,
                                "cost": bad}]},
               ["扩容候选 s1: 一次性费用必须是正整数"])
# 基础三类录入仍需与快照完整对应
expect_422({"capacities": {"s1": 1, "s2": 1, "s3": 1},
            "supplies": SUP, "demands": DEM, "expansions": []},
           ["缺少未关阀管段的流量上限: s4"])
expect_422({"capacities": CAPS5, "supplies": {}, "demands": DEM},
           ["缺少进水点的供给上限: S"])
expect_422({"capacities": CAPS5, "supplies": SUP, "demands": {"H1": 3}},
           ["缺少必保供水点的需求: H2"])
# 空请求体: 基础三类全缺(扩容可空) -> 422
r = client.post(PATH, json={})
check(r.status_code == 422, f"empty body 422, got {r.status_code}")
joined = " | ".join(r.json()["detail"]["errors"])
check("缺少未关阀管段的流量上限" in joined
      and "缺少进水点的供给上限" in joined
      and "缺少必保供水点的需求" in joined, f"empty body errors")

# ---- 预览未新增任何历史方案(只读) ----
plans = client.get("/api/plans?limit=100").json()["plans"]
ids = sorted(p["id"] for p in plans)
check(ids == sorted(set([pid, tie_pid, bad_id])), f"plan ids {ids}")

print(f"capacity expansion API tests: {ok} checks passed")
