"""管段扩容预览 API 端到端测试: FastAPI TestClient + 临时 SQLite。

重点验证: 独立 API 可用、原容量已足够零费用空清单、最低费用扩容组合与同费
字典序决胜、全部候选仍不足时全选最大送达量与总缺口、录入校验 422, 以及
**只读**——不修改当前管网、不写入历史方案、不改变原方案。
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

CAPS = {"s1": 10, "s2": 3, "s3": 10, "s4": 4}
SUP = {"S": 100}
DEM = {"H1": 3, "H2": 4}
BODY = {"capacities": CAPS, "supplies": SUP, "demands": DEM, "candidates": []}

# ---- 原容量已足够: 零费用空清单 ----
r = client.post(f"/api/plans/{pid}/capacity-expansion", json={
    **BODY,
    "candidates": [{"segment_id": "s1", "increment": 5, "cost": 10}],
})
check(r.status_code == 200, f"expansion status {r.status_code} {r.text}")
res = r.json()
check(res["ok"] and res["sufficient"] and res["already_sufficient"]
      and not res["empty"], "already sufficient flags")
check(res["plan_id"] == pid and res["solver"] == "base", "meta fields")
check(res["selected_expansions"] == [] and res["selected_segment_ids"] == []
      and res["total_cost"] == 0, "zero-cost empty selection")
check(res["total_demand"] == 7 and res["delivered_total"] == 7
      and res["shortage_total"] == 0 and res["base_delivered_total"] == 7,
      "already sufficient totals")
check(res["expanded_capacities"] == CAPS, "expanded == base")
check(res["candidate_count"] == 1
      and res["candidates"][0]["segment_id"] == "s1", "candidate echo")
check("只读" in res["message"] and "零费用空清单" in res["message"],
      "already sufficient message")

# ---- 只读: 原方案、历史列表、当前管网均未改变 ----
check(client.get(f"/api/plans/{pid}").json() == before, "plan unchanged")
check(client.get("/api/plans?limit=100").json()["plans"] == plans_before,
      "no plan saved by expansion preview")
check(client.get("/api/network").json() == net_before, "network untouched")

# ---- 原容量不足: 扩容 s1+2 后充足, 最低费用组合 ----
r = client.post(f"/api/plans/{pid}/capacity-expansion", json={
    "capacities": {"s1": 5, "s2": 3, "s3": 10, "s4": 4},
    "supplies": SUP, "demands": DEM,
    "candidates": [{"segment_id": "s1", "increment": 2, "cost": 8},
                   {"segment_id": "s4", "increment": 3, "cost": 4}],
})
check(r.status_code == 200, f"expansion needed status {r.status_code}")
res = r.json()
check(res["ok"] and res["sufficient"] and not res["already_sufficient"],
      "expansion flags")
check(res["selected_segment_ids"] == ["s1"] and res["total_cost"] == 8,
      "selected s1")
sel = res["selected_expansions"][0]
check(sel["segment_id"] == "s1" and sel["base_capacity"] == 5
      and sel["increment"] == 2 and sel["expanded_capacity"] == 7
      and sel["cost"] == 8, f"selected row {sel}")
check(res["expanded_capacities"] == {"s1": 7, "s2": 3, "s3": 10, "s4": 4},
      "expanded capacities")
check(res["base_delivered_total"] == 5 and res["delivered_total"] == 7
      and res["shortage_total"] == 0, "expansion totals")
summary = {ns["node_id"]: (ns["delivered"], ns["shortage"])
           for ns in res["node_summary"]}
check(summary == {"H1": (3, 0), "H2": (4, 0)},
      f"expansion per-node {summary}")

# ---- 并联管网: 同费按管段编号升序列表字典序决胜 ----
NET_PAR = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "X", "role": "normal"},
        {"id": "H", "role": "must_keep"}, {"id": "L", "role": "leak"},
    ],
    "segments": [
        {"id": "a", "endpoint_a": "S", "endpoint_b": "X"},
        {"id": "b", "endpoint_a": "S", "endpoint_b": "X"},
        {"id": "c", "endpoint_a": "X", "endpoint_b": "H"},
        {"id": "d", "endpoint_a": "X", "endpoint_b": "L"},
    ],
    "valves": [{"id": "V1", "segment_id": "d", "cost": 1}],
}
r = client.post("/api/plan", json=NET_PAR)
par = r.json()
check(par["feasible"] and par["closed_valves"] == ["V1"], "parallel plan")
parid = par["id"]
r = client.post(f"/api/plans/{parid}/capacity-expansion", json={
    "capacities": {"a": 3, "b": 3, "c": 10},
    "supplies": {"S": 100}, "demands": {"H": 8},
    "candidates": [{"segment_id": "b", "increment": 2, "cost": 5},
                   {"segment_id": "a", "increment": 2, "cost": 5},
                   {"segment_id": "c", "increment": 9, "cost": 1}],
})
check(r.status_code == 200, f"tie status {r.status_code}")
res = r.json()
check(res["sufficient"] and res["total_cost"] == 5
      and res["selected_segment_ids"] == ["a"],
      f"tie broken by segment id {res['selected_segment_ids']}")
check(res["combination_count"] == 8
      and res["feasible_combination_count"] == 6, "tie combination counts")

# ---- 全部候选仍不足: 全选后的最大送达量与总缺口 ----
r = client.post(f"/api/plans/{parid}/capacity-expansion", json={
    "capacities": {"a": 3, "b": 3, "c": 10},
    "supplies": {"S": 100}, "demands": {"H": 20},
    "candidates": [{"segment_id": "a", "increment": 2, "cost": 5},
                   {"segment_id": "b", "increment": 2, "cost": 5}],
})
check(r.status_code == 200, f"insufficient status {r.status_code}")
res = r.json()
check(not res["ok"] and not res["sufficient"]
      and res["reason_code"] == "INSUFFICIENT_CAPACITY", "insufficient flags")
check(res["delivered_total"] == 10 and res["shortage_total"] == 10
      and res["total_demand"] == 20, "insufficient totals")
check(res["total_cost"] is None and res["selected_expansions"] == [],
      "insufficient no plan")
check(res["all_selected_segment_ids"] == ["a", "b"]
      and res["all_selected_cost_total"] == 10, "all selected echo")
check(res["expanded_capacities"] == {"a": 5, "b": 5, "c": 10},
      "insufficient expanded capacities")
ns = res["node_summary"][0]
check(ns["node_id"] == "H" and ns["delivered"] == 10
      and ns["shortage"] == 10, f"insufficient node summary {ns}")

# ---- 无必保点: 零需求 -> 原容量已足够 ----
NET_NO_KEEP = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "A", "role": "normal"},
        {"id": "L", "role": "leak"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "A"},
        {"id": "p2", "endpoint_a": "A", "endpoint_b": "L"},
    ],
    "valves": [{"id": "V1", "segment_id": "p2", "cost": 1}],
}
r = client.post("/api/plan", json=NET_NO_KEEP)
nk = r.json()
check(nk["feasible"] and nk["closed_valves"] == ["V1"], "no-keep plan saved")
nkid = nk["id"]
r = client.post(f"/api/plans/{nkid}/capacity-expansion", json={
    "capacities": {"p1": 5}, "supplies": {"S": 9}, "demands": {},
    "candidates": [{"segment_id": "p1", "increment": 3, "cost": 2}],
})
check(r.status_code == 200, f"empty status {r.status_code}")
res = r.json()
check(res["ok"] and res["sufficient"] and res["already_sufficient"]
      and res["empty"], "empty flags")
check(res["total_demand"] == 0 and res["delivered_total"] == 0
      and res["total_cost"] == 0 and res["selected_expansions"] == [],
      "empty zeros")
check("没有必保供水点" in res["message"], "empty message")

# ---- 空请求体: 缺少全部录入 -> 422 ----
r = client.post(f"/api/plans/{pid}/capacity-expansion", json={})
check(r.status_code == 422, f"empty body 422, got {r.status_code}")
errs = r.json()["detail"]["errors"]
joined = " | ".join(errs)
check("缺少未关阀管段的流量上限" in joined
      and "缺少进水点的供给上限" in joined
      and "缺少必保供水点的需求" in joined, f"empty body errors {errs}")

# ---- 方案不存在 ----
r = client.post("/api/plans/9999/capacity-expansion", json=BODY)
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
bad_plan = r.json()
check(bad_plan["feasible"] is False, "infeasible plan saved")
bad_id = bad_plan["id"]
r = client.post(f"/api/plans/{bad_id}/capacity-expansion", json=BODY)
check(r.status_code == 422, f"infeasible expansion {r.status_code}")
errs = r.json()["detail"]["errors"]
check(any("不可行" in e and str(bad_id) in e for e in errs),
      f"infeasible msg {errs}")


# ---- 录入校验: 422 + detail.errors ----
def expect_422(body, keys):
    r = client.post(f"/api/plans/{pid}/capacity-expansion", json=body)
    check(r.status_code == 422, f"422 for {keys}: got {r.status_code} {r.text}")
    errs = r.json()["detail"]["errors"]
    joined = " | ".join(errs)
    for key in keys:
        check(key in joined, f"error contains {key!r}; got: {errs}")


# 候选数量超过 6 个
expect_422({**BODY, "candidates": [
    {"segment_id": f"s{i}", "increment": 1, "cost": 1} for i in range(1, 8)]},
    ["扩容候选数量为 7, 超过上限 6 个"])
# 管段重复 / 已关阀管段 / 不存在的管段
expect_422({**BODY, "candidates": [
    {"segment_id": "s1", "increment": 1, "cost": 1},
    {"segment_id": "s1", "increment": 2, "cost": 2}]},
    ["扩容候选管段重复: s1"])
expect_422({**BODY, "candidates": [
    {"segment_id": "s5", "increment": 1, "cost": 1}]},
    ["扩容候选管段 s5 不是方案快照中的未关阀管段"])
expect_422({**BODY, "candidates": [
    {"segment_id": "zz", "increment": 1, "cost": 1}]},
    ["扩容候选管段 zz 不是方案快照中的未关阀管段"])
# 缺少管段编号
expect_422({**BODY, "candidates": [{"increment": 1, "cost": 1}]},
           ["扩容候选 #1: 缺少管段编号"])
# 增量/费用非正整数
for bad in [0, -1, 1.5, "5", True, None]:
    expect_422({**BODY, "candidates": [
        {"segment_id": "s1", "increment": bad, "cost": 1}]},
        ["管段 s1 的扩容增量必须是正整数"])
    expect_422({**BODY, "candidates": [
        {"segment_id": "s1", "increment": 1, "cost": bad}]},
        ["管段 s1 的一次性费用必须是正整数"])
# 沿用容量校核的三类录入校验
expect_422({"capacities": {"s1": 1, "s2": 1, "s3": 1},
            "supplies": SUP, "demands": DEM, "candidates": []},
           ["缺少未关阀管段的流量上限: s4"])
expect_422({"capacities": {**CAPS, "s5": 1},
            "supplies": SUP, "demands": DEM, "candidates": []},
           ["提供了非未关阀管段的流量上限(多余编号): s5"])
expect_422({"capacities": CAPS, "supplies": {}, "demands": DEM,
            "candidates": []},
           ["缺少进水点的供给上限: S"])
expect_422({"capacities": CAPS, "supplies": SUP,
            "demands": {"H1": 3}, "candidates": []},
           ["缺少必保供水点的需求: H2"])
expect_422({"capacities": {**CAPS, "s1": 0}, "supplies": SUP,
            "demands": DEM, "candidates": []},
           ["管段 s1 的流量上限必须是正整数"])

# ---- 预览未新增任何历史方案(只读) ----
plans = client.get("/api/plans?limit=100").json()["plans"]
ids = sorted(p["id"] for p in plans)
check(ids == sorted(set([pid, parid, nkid, bad_id])), f"plan ids {ids}")
check(client.get(f"/api/plans/{pid}").json() == before,
      "plan still unchanged at end")
check(client.get("/api/network").json() == net_before,
      "network still untouched at end")

print(f"capacity expansion API tests: {ok} checks passed")
