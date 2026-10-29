# -*- coding: utf-8 -*-
"""B4 craft.py 自测脚本（沙箱数据目录，不触碰共享 data/）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import core
core.DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox_data")
import modules.craft as C
sess = {"id": 250574, "name": "佳H", "real_name": "佳H", "enterprise_id": 16807}
ok = lambda m: print("PASS |", m)

# ================= pub_craft =================
r = C._pc_list({"pageNo": 1, "pageSize": 5}, sess)
assert r["count"] == 335 and r["data"]["list"][0]["name"] == "JH测试1工序"
assert C._pc_list({"tab": "启用", "pageSize": 400}, sess)["count"] == 335
assert C._pc_list({"tab": "禁用", "pageSize": 400}, sess)["count"] == 0
assert C._pc_list({"is_enable": 1, "pageSize": 400}, sess)["count"] == 335
assert C._pc_list({"name": "JH", "pageSize": 50}, sess)["count"] == 6
assert C._pc_list({"workshop_id": 823440, "pageSize": 400}, sess)["count"] >= 1
assert C._pc_list({"produce_line_id": 946727, "pageSize": 400}, sess)["count"] >= 1
ok("pub_craft list（tab/is_enable/简拼/车间/产线筛选）")

a = C._pc_add({"name": "测试工序X", "code": "GX999", "price_mode": 2, "unit_money": "35.5",
               "standard_working_seconds": 3725, "unit_id": 946731, "workshop_id": 823441,
               "produce_line_ids": [946727, 946730], "remark": "B4自测"}, sess)
row = a["data"]
assert row["unit_money"] == "35.50000000" and row["unit_money_text"] == "35.5元/小时"
assert row["standard_working_name"] == "1小时2分钟5秒" and row["price_mode_name"] == "计时"
assert row["workshop_path_names"] == "下料车间/切割组" and row["produce_line_names"] == "车削,磨削"
assert row["unit_relation"] == "生产1个计划数，需要1个" and row["qr_code_url"].endswith(str(row["id"]))
ok("pub_craft add（计价/单价8位/工时格式化/车间路径/产线/单位关系/二维码）")

a2 = C._pc_add({"name": "外协工序Y", "need_outsource": 1, "quality_type": 1, "is_open_clock": 1}, sess)
r2 = a2["data"]
assert (r2["quality_type"], r2["is_open_clock"], r2["need_scrap_rework"]) == (3, 2, 2) and r2["need_outsource"] == 1
assert C._pc_add({"name": ""}, sess)["msg"] == "请输入工序名"
assert C._pc_add({"name": "Z", "unit_money": "2000000"}, sess)["msg"] == "工资单价最大不能超过1000000"
assert C._pc_add({"name": "Z", "standard_working_seconds": 6000000}, sess)["msg"] == "标准工时的取值范围为大于等于0，小于等于99999分钟"
ok("pub_craft add 校验与开关联动（外协⇒不需检验+不计时+不返工）")

cid = row["id"]
e = C._pc_edit({"id": cid, "unit_money": "100.1", "price_mode": 1}, sess)
assert e["data"]["unit_money"] == "100.10000000" and e["data"]["unit_money_text"] == "100.1元/个"
assert C._pc_toggle("is_open_clock")({"id": cid, "is_open_clock": 1}, sess)["data"]["need_outsource"] == 2
assert C._pc_update_quality_type({"id": cid, "quality_type": 1}, sess)["data"]["quality_type_name"] == "全检"
assert C.ROUTES[("pub_craft", "enable")]({"id": cid}, sess)["success"]
assert C.ROUTES[("pub_craft", "disable")]({"id": cid}, sess)["success"]
assert C._pc_batch_edit({"ids": "%s,%s" % (cid, r2["id"]), "unit_money": "5"}, sess)["success"]
assert core.Store("crafts").get(cid)["unit_money"] == "5.00000000"
assert C._pc_del({"id": cid}, sess)["success"]
assert all(str(x["id"]) != str(cid) for x in C._pc_list({"pageSize": 400}, sess)["data"]["list"])
assert C._pc_detail({"id": r2["id"]}, sess)["success"]
assert C._pc_import_template_header({}, sess)["data"]["required_titles"] == ["工序名", "检验方案", "是否需外协", "计价方式"]
ok("pub_craft edit/开关/检验方案/启停/批量修改/del/detail/import_template_header")

# ================= craft_line =================
r = C._cl_list({"pageNo": 1, "pageSize": 10}, sess)
assert r["count"] == 25 and r["data"]["list"][0]["code"] == "种植工艺"
seed = r["data"]["list"][0]
pid = seed["craft_list"][0]["pub_craft_id"]
assert C._cl_list({"pub_craft_id": pid, "pageSize": 50}, sess)["count"] >= 1
assert C._cl_list({"name": "工艺", "pageSize": 50}, sess)["count"] == 15
assert C._cl_list({"code": "GYLX", "pageSize": 50}, sess)["count"] >= 1
sorted_r = C._cl_list({"sortField": "code", "sortOrder": "ascend", "pageSize": 50}, sess)
codes = [x["code"] for x in sorted_r["data"]["list"]]
assert codes == sorted(codes)
ok("craft_line list（编号/名称/工序筛选+远程排序）")

crafts = core.Store("crafts").all()
ids = [c["id"] for c in crafts[:3]]
la = C._cl_add({"name": "B4测试路线", "pub_craft_ids": ids}, sess)["data"]
assert la["code"] == "GYLX%s001" % core.now("%Y%m"), la["code"]
assert [x["order_number"] for x in la["craft_list"]] == [0, 1, 2]
assert la["craft_names"] == ",".join(c["name"] for c in crafts[:3])
assert la["craft_list"][0]["pub_craft"]["id"] == ids[0]
la2 = C._cl_add({"name": "B4测试路线2", "pub_craft_ids": ids,
                 "craft_list": [{"pub_craft_id": ids[0], "unit_money": "9.5", "price_mode": 2}]}, sess)["data"]
cl0 = la2["craft_list"][0]
assert cl0["unit_money"] == "9.50000000" and cl0["price_mode_name"] == "计时" and cl0["price_mode_tag_class"] == "1"
la3 = C._cl_add({"name": "手填编号", "code": "001", "pub_craft_ids": ids[:1]}, sess)["data"]
assert la3["code"] == "001"
assert C._cl_add({"name": "", "pub_craft_ids": ids}, sess)["msg"] == "请输入工艺路线名称"
assert C._cl_add({"name": "X", "pub_craft_ids": []}, sess)["msg"] == "请至少选择一条工序"
ok("craft_line add（GYLX+YYYYMM+3位自动编号/顺序craft_list/行级覆盖/手填编号/校验）")

le = C._cl_edit({"id": la2["id"], "name": "B4路线改", "pub_craft_ids": ids[:2]}, sess)["data"]
assert le["name"] == "B4路线改" and le["code"] == la2["code"] and len(le["craft_list"]) == 2
assert C._cl_detail({"id": la2["id"]}, sess)["data"]["craft_names"] == le["craft_names"]
s = C._cl_selector({"keyword": "B4", "pageSize": 10}, sess)
assert s["count"] >= 2 and s["data"]["list"][0]["craft_num"] >= 1 and "display_craft_names" in s["data"]["list"][0]
assert C._cl_selector({"pub_craft_id": ids[0], "pageSize": 50}, sess)["count"] >= 1
assert C._cl_del({"id": la3["id"]}, sess)["success"]
assert all(x["id"] != la3["id"] for x in C._cl_list({"pageSize": 50}, sess)["data"]["list"])
ok("craft_line edit(编号只读)/detail/selector/del")

# ================= goods_craft =================
goods = sorted([g for g in core.Store("goods").all() if g.get("craft_list")],
               key=lambda g: -len(g["craft_list"]))
g0, g1, g2 = goods[0], goods[1], goods[2]
gid = g0["id"]
n0 = len(g0["craft_list"])
rows = C._gc_get_list({"goods_id": gid}, sess)["data"]
assert len(rows) == n0 and [x["order_number"] for x in rows] == list(range(n0))
ok("goods_craft get_goods_craft_list（惰性迁移+order_number 0起连续）")

used = {str(r["pub_craft_id"]) for r in rows}
new_craft = [c for c in core.Store("crafts").all() if str(c["id"]) not in used][0]
dropped = rows[-1]["id"]
sub = [dict(x) for x in rows[-2::-1]] + [{"pub_craft_id": new_craft["id"], "unit_money": "8.8"}]
up = C._gc_update({"goods_id": gid, "craft_list": sub}, sess)
assert up["success"]
after = C._gc_get_list({"goods_id": gid}, sess)["data"]
assert len(after) == n0 and [x["order_number"] for x in after] == list(range(n0))
assert after[0]["id"] == rows[-2]["id"]
assert dropped not in set(x["id"] for x in after)
assert set(x["id"] for x in after[:-1]) == set(x["id"] for x in rows[:-1])
assert after[-1]["pub_craft_id"] == new_craft["id"] and after[-1]["unit_money"] == "8.80000000"
assert len(core.Store("goods").get(gid)["craft_list"]) == n0
ok("goods_craft update_goods_craft（全量提交：下标重排/缺席删/新行插/旧行保id/goods同步）")

ba = C._gc_batch_add_by_pub_craft_ids({"goods_id": gid, "pub_craft_ids": [new_craft["id"]]}, sess)
tail = C._gc_get_list({"goods_id": gid}, sess)["data"][-1]
assert ba["success"] and tail["order_number"] == n0
gl = C._gc_list({"goods_keyword": g0["name"], "pageSize": 300}, sess)
assert gl["count"] >= n0 + 1
ed = C._gc_edit({"id": tail["id"], "unit_money": "66"}, sess)
assert ed["data"]["unit_money"] == "66.00000000"
bd = C._gc_batch_del({"ids": str(tail["id"])}, sess)
assert bd["success"] and len(core.Store("goods").get(gid)["craft_list"]) == n0
ok("goods_craft batch_add_by_pub_craft_ids / list / edit / batch_del")

anchor = C._gc_get_list({"goods_id": g1["id"]}, sess)["data"][0]["pub_craft_id"]
bi = C._goods_batch_insert({"goods_ids": [g1["id"], g2["id"]], "pub_craft_id": new_craft["id"],
                            "prev_pub_craft_id": anchor}, sess)
assert bi["success"]
r1 = C._gc_get_list({"goods_id": g1["id"]}, sess)["data"]
assert r1[1]["pub_craft_id"] == new_craft["id"]
g2_rows = C._gc_get_list({"goods_id": g2["id"]}, sess)["data"]
g2_anchor = str(anchor) in set(str(x["pub_craft_id"]) for x in g2_rows)
if g2_anchor:
    assert bi["data"]["success_count"] == 2
else:
    assert bi["data"]["success_count"] == 1 and set(f["goods_name"] for f in bi["data"]["fail_array"]) == set([g2["name"]])
assert bi["data"]["success_count"] + len(bi["data"]["fail_array"]) == 2
bi3 = C._goods_batch_insert({"goods_ids": [g2["id"]], "pub_craft_id": new_craft["id"]}, sess)
assert bi3["success"] and C._gc_get_list({"goods_id": g2["id"]}, sess)["data"][-1]["pub_craft_id"] == new_craft["id"]
assert C._goods_batch_insert({"goods_ids": [], "pub_craft_id": 1}, sess)["msg"] == "请勾选商品"
assert C._goods_batch_insert({"goods_ids": [g1["id"]], "pub_craft_id": 1, "prev_pub_craft_id": 1, "next_pub_craft_id": 2}, sess)["msg"] == "前面的工序和后面的工序最多只能选择一个。"
ok("goods batch_insert_goods_craft（prev定位/无锚点失败/末尾追加/互斥与必填校验）")

# ================= waste_item =================
w = C._wi_list({"pageNo": 1, "pageSize": 10}, sess)
assert w["count"] == 8 and w["data"]["list"][0]["name"]
assert C._wi_list({"name": "毛刺", "pageSize": 20}, sess)["count"] >= 1
wa = C._wi_add({"name": "B4不良项"}, sess)["data"]
assert wa["code"] == "B4不良项" and not C._wi_add({"name": "B4不良项"}, sess)["success"]
assert C._wi_edit({"id": wa["id"], "name": "B4不良项改"}, sess)["data"]["name"] == "B4不良项改"
assert C._wi_del({"id": wa["id"]}, sess)["success"]
assert C._wi_list({"name": "B4不良项改", "pageSize": 20}, sess)["count"] == 0
ok("waste_item list/add(重名校验)/edit/del")

print("")
print("=== B4 craft.py 全部自测通过（本模块 ROUTES=%d 个端点）===" % len(C.ROUTES))
