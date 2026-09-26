"""plan_capacity_expansion 管段扩容预览测试。

覆盖: 原容量已足够返回零费用空清单、枚举候选组合用整数最大流检验、最低
费用选取、同费按管段编号升序列表字典序决胜、全部候选仍不足(全选后最大
送达量与总缺口)、扩容后净流量绝对值受原上限+增量约束、无必保点空清单、
候选录入校验(至多 6 个/不同未关阀管段/正整数增量与费用)。
"""
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


def expect_errors(snapshot, closed, caps, sup, dem, exps, keys):
    try:
        plan_capacity_expansion(snapshot, closed, caps, sup, dem, exps)
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
SUP = {"S": 100}
DEM = {"H1": 3, "H2": 4}

# 1. 原容量已足够: 即使给了候选, 也返回零费用空清单
r = plan_capacity_expansion(
    DEMO_NETWORK, CV,
    {"s1": 10, "s2": 3, "s3": 10, "s4": 4}, SUP, DEM,
    [{"segment_id": "s1", "increment": 1, "cost": 1}])
check(r["ok"] and r["sufficient"] and r["satisfied"] and not r["empty"],
      "baseline sufficient flags")
check(r["selected_segment_ids"] == [] and r["total_cost"] == 0,
      "zero-cost empty list")
check(r["delivered_total"] == 7 and r["shortage_total"] == 0
      and r["total_demand"] == 7, "baseline totals")
check(r["baseline_delivered_total"] == 7, "baseline echo")
check(r["candidate_count"] == 1 and r["combination_count"] == 2
      and r["feasible_combination_count"] == 2, "combination stats")
check("无需扩容" in r["message"] and "只读" in r["message"], "message")
# 生效上限即原上限
check(r["effective_capacities"] == {"s1": 10, "s2": 3, "s3": 10, "s4": 4},
      "effective caps unchanged")

# 2. 原容量不足(干管 s1=5, 送达 5 缺 2), 需要扩容
#    s1+2 费 10 可行; s4+2 费 3 单独不可行(s1 仍是瓶颈)
r = plan_capacity_expansion(
    DEMO_NETWORK, CV,
    {"s1": 5, "s2": 3, "s3": 10, "s4": 4}, SUP, DEM,
    [{"segment_id": "s1", "increment": 2, "cost": 10},
     {"segment_id": "s4", "increment": 2, "cost": 3}])
check(r["ok"] and r["satisfied"], "expansion feasible")
check(r["selected_segment_ids"] == ["s1"], f"selected {r['selected_segment_ids']}")
check(r["total_cost"] == 10, "cost")
check(r["delivered_total"] == 7 and r["shortage_total"] == 0, "expanded totals")
check(r["baseline_delivered_total"] == 5, "baseline delivered")
check(r["combination_count"] == 4 and r["feasible_combination_count"] == 2,
      "4 combos, 2 feasible")
sel = r["selected_expansions"][0]
check(sel["segment_id"] == "s1" and sel["capacity"] == 5
      and sel["increment"] == 2 and sel["expanded_capacity"] == 7
      and sel["cost"] == 10, f"selected row {sel}")
# 扩容后各必保点均满足
check({ns["node_id"]: (ns["delivered"], ns["shortage"])
       for ns in r["node_summary"]} == {"H1": (3, 0), "H2": (4, 0)},
      "node summary after expansion")
# 生效上限: 仅 s1 增加
check(r["effective_capacities"] == {"s1": 7, "s2": 3, "s3": 10, "s4": 4},
      f"effective {r['effective_capacities']}")
check(r["all_candidates_cost_total"] == 13, "all candidate cost reference")

# 3. 费用优先: 便宜的可行组合优先; 贵候选即使编号小也不选
r = plan_capacity_expansion(
    DEMO_NETWORK, CV,
    {"s1": 5, "s2": 3, "s3": 10, "s4": 4}, SUP, DEM,
    [{"segment_id": "s1", "increment": 2, "cost": 9},
     {"segment_id": "s2", "increment": 1, "cost": 1}])
check(r["selected_segment_ids"] == ["s1"] and r["total_cost"] == 9,
      "cheapest feasible picked")
# 同一管段不允许两个候选(录入校验段覆盖); 便宜的不可行候选(s2)不被选择
check(r["feasible_combination_count"] == 2
      and r["combination_count"] == 4, "only s1 combos feasible")

# 4. 同费按管段编号升序列表字典序决胜
#    双支路: S-a-X-b-H / S-c-Y-d-H; 基础 a=c=3(b,d=10), 总 6 < 需求 8;
#    单扩 a+4 或 c+4 都可行(7+3=10), 同费 5 -> 取编号小的 a
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
ptie = solve(NET_TIE)
check(ptie["feasible"] and ptie["closed_valves"] == [], "tie plan")
r = plan_capacity_expansion(
    NET_TIE, [], {"a": 3, "b": 10, "c": 3, "d": 10},
    {"S": 100}, {"H": 8},
    [{"segment_id": "c", "increment": 4, "cost": 5},
     {"segment_id": "a", "increment": 4, "cost": 5}])
check(r["selected_segment_ids"] == ["a"], f"lex tie {r['selected_segment_ids']}")
check(r["total_cost"] == 5 and r["feasible_combination_count"] == 3
      and r["combination_count"] == 4, "tie stats")
# 候选回显按管段编号升序(a 在 c 前, 与录入顺序无关)
check([c["segment_id"] for c in r["candidates"]] == ["a", "c"],
      "candidates sorted by segment id")
# 费用不同: c 便宜 -> 选 c(字典序不抵消费用)
r = plan_capacity_expansion(
    NET_TIE, [], {"a": 3, "b": 10, "c": 3, "d": 10},
    {"S": 100}, {"H": 8},
    [{"segment_id": "a", "increment": 4, "cost": 5},
     {"segment_id": "c", "increment": 4, "cost": 1}])
check(r["selected_segment_ids"] == ["c"] and r["total_cost"] == 1,
      "cost beats lexicographic")

# 5. 全部候选仍不足: 全选后的最大送达量与总缺口, 不产生方案(total_cost null)
r = plan_capacity_expansion(
    NET_TIE, [], {"a": 3, "b": 4, "c": 3, "d": 4},
    {"S": 100}, {"H": 9},
    [{"segment_id": "a", "increment": 1, "cost": 1},
     {"segment_id": "c", "increment": 1, "cost": 1}])
check(not r["ok"] and r["reason_code"] == "INSUFFICIENT_EXPANSION"
      and not r["sufficient"], "insufficient flags")
check(r["delivered_total"] == 8 and r["shortage_total"] == 1
      and r["total_demand"] == 9, "insufficient totals")
check(r["total_cost"] is None and r["selected_segment_ids"] == []
      and r["selected_expansions"] == [], "no plan produced")
check(r["feasible_combination_count"] == 0 and r["combination_count"] == 4,
      "no feasible combo")
check(r["all_candidates_cost_total"] == 2, "all candidate cost reference")
# 全选后生效上限与各必保点缺口
check(r["effective_capacities"] == {"a": 4, "b": 4, "c": 4, "d": 4},
      "effective caps all selected")
check(r["node_summary"][0]["delivered"] == 8
      and r["node_summary"][0]["shortage"] == 1, "insufficient node row")
check("总缺口 1" in r["message"], "insufficient message")
# 无候选且不足: 全选即空集
r0 = plan_capacity_expansion(
    DEMO_NETWORK, CV,
    {"s1": 5, "s2": 3, "s3": 10, "s4": 4}, SUP, DEM, [])
check(not r0["ok"] and r0["delivered_total"] == 5
      and r0["shortage_total"] == 2 and r0["total_cost"] is None
      and r0["combination_count"] == 1 and r0["candidate_count"] == 0,
      "no candidate insufficient")

# 6. 扩容后管段净流量绝对值受「原上限 + 增量」约束(不能双向重复计)
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
solve(NET_LINE)
# 原 p2=6, 需求 10; 扩 p2 +3 => 9 仍缺 1; 扩 p2 +4 => 10 可行
r = plan_capacity_expansion(
    NET_LINE, [], {"p1": 10, "p2": 6}, {"S": 100}, {"H": 10},
    [{"segment_id": "p2", "increment": 3, "cost": 2}])
check(not r["ok"] and r["delivered_total"] == 9
      and r["shortage_total"] == 1, "net flow bound after expansion +3")
r = plan_capacity_expansion(
    NET_LINE, [], {"p1": 10, "p2": 6}, {"S": 100}, {"H": 10},
    [{"segment_id": "p2", "increment": 4, "cost": 2}])
check(r["ok"] and r["selected_segment_ids"] == ["p2"]
      and r["delivered_total"] == 10, "expansion +4 sufficient")

# 7. 无必保点: 零费用空清单(仍需完整录入未关阀管段与进水点)
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
nk = solve(NET_NO_KEEP)
r = plan_capacity_expansion(
    NET_NO_KEEP, ["V1"], {"p1": 5}, {"S": 9}, {},
    [{"segment_id": "p1", "increment": 1, "cost": 1}])
check(r["ok"] and r["sufficient"] and r["empty"], "no-keep flags")
check(r["selected_segment_ids"] == [] and r["total_cost"] == 0
      and r["total_demand"] == 0 and r["delivered_total"] == 0
      and r["shortage_total"] == 0, "no-keep zeros")
check(r["candidate_count"] == 1 and r["combination_count"] == 2
      and r["feasible_combination_count"] == 1, "no-keep stats")

# 8. 备援方案快照上同样可用
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
# 未关阀管段只有 p3, 原上限 4 < 需求 5; 扩 p3 +1 费 2 可行
r = plan_capacity_expansion(
    NET_FO, fo["closed_valves"], {"p3": 4}, {"S": 10}, {"H": 5},
    [{"segment_id": "p3", "increment": 1, "cost": 2}])
check(r["open_segment_ids"] == ["p3"], "failover open segments")
check(r["ok"] and r["selected_segment_ids"] == ["p3"]
      and r["total_cost"] == 2 and r["delivered_total"] == 5,
      "failover expansion")

# ---- 录入校验 ----
CAPS5 = {"s1": 5, "s2": 3, "s3": 10, "s4": 4}
# 超过 6 个
expect_errors(DEMO_NETWORK, CV, CAPS5, SUP, DEM,
              [{"segment_id": "s1", "increment": 1, "cost": 1}] * 7,
              ["扩容候选数量为 7, 超过上限 6 个"])
# 扩容管段不是未关阀管段(已关阀 s5/s6)
expect_errors(DEMO_NETWORK, CV, CAPS5, SUP, DEM,
              [{"segment_id": "s5", "increment": 1, "cost": 1}],
              ["扩容候选 #1: 扩容管段 s5 不是方案快照中未关阀的管段"])
expect_errors(DEMO_NETWORK, CV, CAPS5, SUP, DEM,
              [{"segment_id": "zz", "increment": 1, "cost": 1}],
              ["扩容候选 #1: 扩容管段 zz 不是方案快照中未关阀的管段"])
# 同一管段多个候选
expect_errors(DEMO_NETWORK, CV, CAPS5, SUP, DEM,
              [{"segment_id": "s1", "increment": 1, "cost": 1},
               {"segment_id": "s1", "increment": 2, "cost": 2}],
              ["管段 s1 出现了多个扩容候选"])
# 缺少扩容管段
expect_errors(DEMO_NETWORK, CV, CAPS5, SUP, DEM,
              [{"increment": 1, "cost": 1}],
              ["扩容候选 #1: 必须选择扩容管段"])
# 增量非正整数
for bad in [0, -1, 1.5, "2", True, None]:
    expect_errors(DEMO_NETWORK, CV, CAPS5, SUP, DEM,
                  [{"segment_id": "s1", "increment": bad, "cost": 1}],
                  ["扩容候选 s1: 增量必须是正整数"])
# 一次性费用非正整数
for bad in [0, -2, 3.5, "1", False, None]:
    expect_errors(DEMO_NETWORK, CV, CAPS5, SUP, DEM,
                  [{"segment_id": "s1", "increment": 1, "cost": bad}],
                  ["扩容候选 s1: 一次性费用必须是正整数"])
# 非数组
expect_errors(DEMO_NETWORK, CV, CAPS5, SUP, DEM, "x",
              ["expansions 必须是数组"])
# 非对象元素
expect_errors(DEMO_NETWORK, CV, CAPS5, SUP, DEM, [5],
              ["扩容候选 #1: 必须是对象"])

# ---- 基础录入沿用容量校核的完整对应校验 ----
expect_errors(DEMO_NETWORK, CV, {"s1": 1, "s2": 1, "s3": 1}, SUP, DEM, [],
              ["缺少未关阀管段的流量上限: s4"])
expect_errors(DEMO_NETWORK, CV, CAPS5, {}, DEM, [],
              ["缺少进水点的供给上限: S"])
expect_errors(DEMO_NETWORK, CV, CAPS5, SUP, {"H1": 3}, [],
              ["缺少必保供水点的需求: H2"])

# 计划阀门不存在于快照
expect_errors(DEMO_NETWORK, ["V9"], CAPS5, SUP, DEM, [],
              ["方案关闭的阀门 V9 不存在于方案快照的管网中"])

print(f"capacity expansion tests: {passed} checks passed")
