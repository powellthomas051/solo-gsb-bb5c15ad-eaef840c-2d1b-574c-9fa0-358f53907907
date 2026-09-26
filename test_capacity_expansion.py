"""plan_capacity_expansion 管段扩容预览测试。

覆盖: 沿用容量校核录入(方案快照/未关阀管段流量上限/进水点供给上限/必保点
需求)的完整对应校验、扩容候选校验(至多 6 个/不同未关阀管段/正整数增量与
一次性费用)、原容量已足够时零费用空清单、枚举组合的最低费用与同费管段编号
升序列表字典序决胜、扩容后上限 = 原上限+增量(净流量绝对值受同一上限约束)、
全部候选仍不足时全选后的最大送达量与总缺口、无必保点零需求、确定性及
只读(不改入参)。
"""
import copy
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from solver import (  # noqa: E402
    NetworkValidationError, plan_capacity_expansion, solve, solve_failover,
)
from demo import DEMO_NETWORK  # noqa: E402

passed = 0


def check(cond, msg):
    global passed
    assert cond, "FAIL: " + msg
    passed += 1


def expect_errors(snapshot, closed, capacities, supplies, demands, cands, keys):
    try:
        plan_capacity_expansion(snapshot, closed, capacities, supplies,
                                demands, cands)
    except NetworkValidationError as exc:
        joined = " | ".join(exc.errors)
        for key in keys:
            check(key in joined, f"error contains {key!r}; got: {exc.errors}")
        return
    raise AssertionError("should have raised: " + str(keys))


# ---- 示例管网: 常规最优方案关闭 V2(s5)+V4(s6), 未关阀管段 s1..s4 ----
DEMO = solve(DEMO_NETWORK)
check(DEMO["feasible"] and DEMO["closed_valves"] == ["V2", "V4"],
      f"demo plan {DEMO.get('closed_valves')}")
CV = DEMO["closed_valves"]
# 未关阀管段: s1(S-A), s2(A-H1), s3(A-B), s4(B-H2); 进水点 S; 必保点 H1,H2
CAPS = {"s1": 10, "s2": 3, "s3": 10, "s4": 4}
SUP = {"S": 100}
DEM = {"H1": 3, "H2": 4}

# 1. 原容量已足够: 零费用空清单(候选仍照常校验与回显)
r = plan_capacity_expansion(DEMO_NETWORK, CV, CAPS, SUP, DEM,
                            [{"segment_id": "s1", "increment": 5, "cost": 10}])
check(r["ok"] and r["sufficient"] and r["already_sufficient"]
      and not r["empty"], "already sufficient flags")
check(r["selected_expansions"] == [] and r["selected_segment_ids"] == []
      and r["total_cost"] == 0, "zero-cost empty selection")
check(r["total_demand"] == 7 and r["delivered_total"] == 7
      and r["shortage_total"] == 0 and r["base_delivered_total"] == 7,
      "already sufficient totals")
check(r["expanded_capacities"] == CAPS, "expanded == base when empty")
check(r["combination_count"] == 2 and r["feasible_combination_count"] == 2,
      "already sufficient combination counts")
check(r["candidate_count"] == 1
      and r["candidates"][0]["segment_id"] == "s1"
      and r["candidates"][0]["increment"] == 5
      and r["candidates"][0]["cost"] == 10, "candidate echo")
check(r["candidates"][0]["endpoint_a"] == "S"
      and r["candidates"][0]["endpoint_b"] == "A"
      and r["candidates"][0]["base_capacity"] == 10, "candidate row fields")
check(r["closed_valves"] == ["V2", "V4"]
      and r["open_segment_ids"] == ["s1", "s2", "s3", "s4"], "echoes")
check("只读" in r["message"] and "零费用空清单" in r["message"]
      and "原容量已足够" in r["message"], "already sufficient message")

# 2. 原容量不足: 干管 s1 上限 5 < 需求 7, 扩容 s1+2 后充足
LOW = {"s1": 5, "s2": 3, "s3": 10, "s4": 4}
CANDS2 = [{"segment_id": "s1", "increment": 2, "cost": 8},
          {"segment_id": "s4", "increment": 3, "cost": 4}]
r = plan_capacity_expansion(DEMO_NETWORK, CV, LOW, SUP, DEM, CANDS2)
check(r["ok"] and r["sufficient"] and not r["already_sufficient"],
      "expansion flags")
check(r["base_delivered_total"] == 5 and r["total_demand"] == 7
      and r["delivered_total"] == 7 and r["shortage_total"] == 0,
      "expansion totals")
check(r["selected_segment_ids"] == ["s1"] and r["total_cost"] == 8,
      "selected s1")
sel = r["selected_expansions"][0]
check(sel["segment_id"] == "s1" and sel["endpoint_a"] == "S"
      and sel["endpoint_b"] == "A" and sel["base_capacity"] == 5
      and sel["increment"] == 2 and sel["expanded_capacity"] == 7
      and sel["cost"] == 8, f"selected row {sel}")
check(r["expanded_capacities"] == {"s1": 7, "s2": 3, "s3": 10, "s4": 4},
      "expanded capacities")
check(r["combination_count"] == 4 and r["feasible_combination_count"] == 2,
      "expansion combination counts")
summary = {ns["node_id"]: (ns["delivered"], ns["shortage"])
           for ns in r["node_summary"]}
check(summary == {"H1": (3, 0), "H2": (4, 0)}, f"expansion summary {summary}")
check("一次性费用合计 8" in r["message"] and "s1" in r["message"],
      "expansion message")

# 3. 并联管网: 同费时按管段编号升序列表字典序决胜
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
plan = solve(NET_PAR)
check(plan["feasible"] and plan["closed_valves"] == ["V1"], "parallel plan")
PCAPS = {"a": 3, "b": 3, "c": 10}
PSUP = {"S": 100}
# a/b 各扩容 +2 均可行且同费 5; c 扩容无用(瓶颈在 a+b)
PCANDS = [{"segment_id": "a", "increment": 2, "cost": 5},
          {"segment_id": "b", "increment": 2, "cost": 5},
          {"segment_id": "c", "increment": 9, "cost": 1}]
r = plan_capacity_expansion(NET_PAR, ["V1"], PCAPS, PSUP, {"H": 8}, PCANDS)
check(r["sufficient"] and r["total_cost"] == 5
      and r["selected_segment_ids"] == ["a"],
      f"tie broken by segment id {r['selected_segment_ids']}")
check(r["combination_count"] == 8 and r["feasible_combination_count"] == 6,
      "parallel combination counts")
check(r["expanded_capacities"] == {"a": 5, "b": 3, "c": 10},
      "parallel expanded capacities")
check(r["base_delivered_total"] == 6, "parallel base delivered")
# 费用优先于字典序: a 费用 9, b 费用 5 -> 选 b
r = plan_capacity_expansion(
    NET_PAR, ["V1"], PCAPS, PSUP, {"H": 8},
    [{"segment_id": "a", "increment": 2, "cost": 9},
     {"segment_id": "b", "increment": 2, "cost": 5}])
check(r["selected_segment_ids"] == ["b"] and r["total_cost"] == 5,
      "cost beats lexicographic")
# 确定性: 相同输入两次调用结果完全一致
r2 = plan_capacity_expansion(NET_PAR, ["V1"], PCAPS, PSUP, {"H": 8}, PCANDS)
r3 = plan_capacity_expansion(NET_PAR, ["V1"], PCAPS, PSUP, {"H": 8}, PCANDS)
check(r2 == r3 and r2["selected_segment_ids"] == ["a"], "deterministic")

# 4. 全部候选仍不足: 全选后的最大送达量与总缺口
r = plan_capacity_expansion(
    NET_PAR, ["V1"], PCAPS, PSUP, {"H": 20},
    [{"segment_id": "a", "increment": 2, "cost": 5},
     {"segment_id": "b", "increment": 2, "cost": 5}])
check(not r["ok"] and not r["sufficient"]
      and r["reason_code"] == "INSUFFICIENT_CAPACITY", "insufficient flags")
check(r["delivered_total"] == 10 and r["shortage_total"] == 10
      and r["total_demand"] == 20, "insufficient totals")
check(r["total_cost"] is None and r["selected_expansions"] == []
      and r["selected_segment_ids"] == [], "insufficient no plan")
check(r["all_selected_segment_ids"] == ["a", "b"]
      and r["all_selected_cost_total"] == 10, "all selected echo")
check(r["expanded_capacities"] == {"a": 5, "b": 5, "c": 10},
      "insufficient expanded capacities")
check(r["feasible_combination_count"] == 0 and r["combination_count"] == 4,
      "insufficient combination counts")
ns = r["node_summary"][0]
check(ns["node_id"] == "H" and ns["delivered"] == 10 and ns["shortage"] == 10,
      f"insufficient node summary {ns}")
allsel = {x["segment_id"]: x for x in r["all_selected_expansions"]}
check(allsel["a"]["expanded_capacity"] == 5
      and allsel["b"]["expanded_capacity"] == 5, "all selected rows")
check("全选" in r["message"] and "总缺口 10" in r["message"],
      "insufficient message")

# 5. 零候选且原容量不足: 全选为空, 最大送达量即原送达量
r = plan_capacity_expansion(DEMO_NETWORK, CV, LOW, SUP, DEM, [])
check(not r["ok"] and r["reason_code"] == "INSUFFICIENT_CAPACITY",
      "no candidates insufficient")
check(r["delivered_total"] == 5 and r["shortage_total"] == 2
      and r["all_selected_expansions"] == []
      and r["all_selected_cost_total"] == 0, "no candidates totals")
check(r["combination_count"] == 1 and r["feasible_combination_count"] == 0,
      "no candidates combination counts")
check(r["expanded_capacities"] == LOW, "no candidates expanded == base")

# 6. 扩容增量作用于净流量绝对值上限: 单管 6+4=10, 不能按双向计
NET_LINE = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "M", "role": "normal"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "M"},
        {"id": "p2", "endpoint_a": "M", "endpoint_b": "H"},
    ],
    "valves": [],
}
plan = solve(NET_LINE)
check(plan["feasible"] and plan["closed_valves"] == [], "line plan")
r = plan_capacity_expansion(
    NET_LINE, [], {"p1": 10, "p2": 6}, {"S": 100}, {"H": 10},
    [{"segment_id": "p1", "increment": 50, "cost": 1},
     {"segment_id": "p2", "increment": 4, "cost": 7}])
check(r["sufficient"] and r["selected_segment_ids"] == ["p2"]
      and r["total_cost"] == 7, "line expansion selects bottleneck")
check(r["expanded_capacities"] == {"p1": 10, "p2": 10}
      and r["delivered_total"] == 10, "line expanded capacities")
check(r["selected_expansions"][0]["expanded_capacity"] == 10,
      "line expanded row")

# 7. 无必保点: 零需求 -> 原容量已足够, 零费用空清单
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
plan = solve(NET_NO_KEEP)
check(plan["feasible"] and plan["closed_valves"] == ["V1"], "no-keep plan")
r = plan_capacity_expansion(NET_NO_KEEP, ["V1"], {"p1": 5}, {"S": 9}, {},
                            [{"segment_id": "p1", "increment": 3, "cost": 2}])
check(r["ok"] and r["sufficient"] and r["already_sufficient"] and r["empty"],
      "empty flags")
check(r["total_demand"] == 0 and r["delivered_total"] == 0
      and r["shortage_total"] == 0 and r["total_cost"] == 0
      and r["selected_expansions"] == [], "empty zeros")
check(r["candidate_count"] == 1 and r["candidates"][0]["segment_id"] == "p1",
      "empty candidate echo")
check("没有必保供水点" in r["message"] and "零费用空清单" in r["message"],
      "empty message")

# 8. 备援方案上同样可用
NET_FO = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "M", "role": "normal"},
        {"id": "L", "role": "leak"}, {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "M"},
        {"id": "p2", "endpoint_a": "M", "endpoint_b": "L"},
        {"id": "p3", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "p1", "cost": 1},
        {"id": "V2", "segment_id": "p2", "cost": 2},
    ],
}
fo = solve_failover(NET_FO)
check(fo["feasible"] and fo["closed_valves"] == ["V1", "V2"], "failover plan")
r = plan_capacity_expansion(NET_FO, fo["closed_valves"], {"p3": 4},
                            {"S": 10}, {"H": 5},
                            [{"segment_id": "p3", "increment": 1, "cost": 2}])
check(r["sufficient"] and r["selected_segment_ids"] == ["p3"]
      and r["total_cost"] == 2 and r["expanded_capacities"] == {"p3": 5},
      "failover expansion")
check(r["base_delivered_total"] == 4, "failover base delivered")

# 9. 只读: 不修改入参
snap = copy.deepcopy(DEMO_NETWORK)
caps_in = dict(LOW)
sup_in = dict(SUP)
dem_in = dict(DEM)
cands_in = copy.deepcopy(CANDS2)
plan_capacity_expansion(snap, CV, caps_in, sup_in, dem_in, cands_in)
check(snap == DEMO_NETWORK and caps_in == LOW and sup_in == SUP
      and dem_in == DEM and cands_in == CANDS2, "inputs untouched")

# ---- 录入校验: 扩容候选 ----
# 候选数量超过 6 个
too_many = [{"segment_id": f"s{i}", "increment": 1, "cost": 1}
            for i in range(1, 8)]
expect_errors(DEMO_NETWORK, CV, CAPS, SUP, DEM, too_many,
              ["扩容候选数量为 7, 超过上限 6 个"])
# 管段重复 / 已关阀管段 / 不存在的管段
expect_errors(DEMO_NETWORK, CV, CAPS, SUP, DEM,
              [{"segment_id": "s1", "increment": 1, "cost": 1},
               {"segment_id": "s1", "increment": 2, "cost": 2}],
              ["扩容候选管段重复: s1"])
expect_errors(DEMO_NETWORK, CV, CAPS, SUP, DEM,
              [{"segment_id": "s5", "increment": 1, "cost": 1}],
              ["扩容候选管段 s5 不是方案快照中的未关阀管段"])
expect_errors(DEMO_NETWORK, CV, CAPS, SUP, DEM,
              [{"segment_id": "zz", "increment": 1, "cost": 1}],
              ["扩容候选管段 zz 不是方案快照中的未关阀管段"])
# 缺少管段编号 / 候选不是对象 / candidates 不是数组
expect_errors(DEMO_NETWORK, CV, CAPS, SUP, DEM,
              [{"increment": 1, "cost": 1}], ["扩容候选 #1: 缺少管段编号"])
expect_errors(DEMO_NETWORK, CV, CAPS, SUP, DEM,
              [{"segment_id": "  ", "increment": 1, "cost": 1}],
              ["扩容候选 #1: 缺少管段编号"])
expect_errors(DEMO_NETWORK, CV, CAPS, SUP, DEM, [1],
              ["扩容候选 #1: 必须是对象"])
expect_errors(DEMO_NETWORK, CV, CAPS, SUP, DEM, "x",
              ["candidates 必须是数组"])
# 增量非正整数
for bad in [0, -1, 1.5, "5", True, None]:
    expect_errors(DEMO_NETWORK, CV, CAPS, SUP, DEM,
                  [{"segment_id": "s1", "increment": bad, "cost": 1}],
                  ["管段 s1 的扩容增量必须是正整数"])
# 一次性费用非正整数
for bad in [0, -2, 1.5, "3", True, None]:
    expect_errors(DEMO_NETWORK, CV, CAPS, SUP, DEM,
                  [{"segment_id": "s1", "increment": 1, "cost": bad}],
                  ["管段 s1 的一次性费用必须是正整数"])
# 原容量已足够时候选仍照常校验
expect_errors(DEMO_NETWORK, CV, CAPS, SUP, DEM,
              [{"segment_id": "s1", "increment": 0, "cost": 1}],
              ["管段 s1 的扩容增量必须是正整数"])

# ---- 录入校验: 沿用容量校核的三类录入(与方案快照完整对应) ----
expect_errors(DEMO_NETWORK, CV, {"s1": 1, "s2": 1, "s3": 1}, SUP, DEM, [],
              ["缺少未关阀管段的流量上限: s4"])
expect_errors(DEMO_NETWORK, CV, {**CAPS, "s5": 1}, SUP, DEM, [],
              ["提供了非未关阀管段的流量上限(多余编号): s5"])
expect_errors(DEMO_NETWORK, CV, CAPS, {}, DEM, [],
              ["缺少进水点的供给上限: S"])
expect_errors(DEMO_NETWORK, CV, CAPS, SUP, {"H1": 3}, [],
              ["缺少必保供水点的需求: H2"])
expect_errors(DEMO_NETWORK, CV, {**CAPS, "s1": 0}, SUP, DEM, [],
              ["管段 s1 的流量上限必须是正整数"])
expect_errors(DEMO_NETWORK, CV, CAPS, {"S": "x"}, DEM, [],
              ["进水点 S 的供给上限必须是正整数"])
expect_errors(DEMO_NETWORK, CV, CAPS, SUP, {"H1": 3, "H2": 1.5}, [],
              ["必保点 H2 的需求必须是正整数"])
expect_errors(DEMO_NETWORK, ["V9"], CAPS, SUP, DEM, [],
              ["方案关闭的阀门 V9 不存在于方案快照的管网中"])
expect_errors(DEMO_NETWORK, CV, [1, 2], SUP, DEM, [],
              ["capacities 必须是以管段编号为键的对象"])

print(f"capacity expansion tests: {passed} checks passed")
