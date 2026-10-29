# -*- coding: utf-8 -*-
"""重建 B4 自测沙箱（原始 api_dump → _selftest_tmp/sandbox_data）。"""
import json, os

DUMP = r"D:\zcoding\报表资料持续检索\跨境报表\_gen_tmp\kgd_reverse\api_dump"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "sandbox_data")


def dump_rows(f):
    return json.load(open(os.path.join(DUMP, f), encoding="utf-8"))["rows"]


def dd_rows(f):
    d = json.load(open(os.path.join(DUMP, f), encoding="utf-8"))
    return d["data"]["list"] if isinstance(d.get("data"), dict) else (d.get("data") or [])


def save(name, rows):
    json.dump(rows, open(os.path.join(OUT, name + ".json"), "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        os.remove(os.path.join(OUT, f))
    save("crafts", dump_rows("craft_all.json"))
    save("routings", dump_rows("routing_all.json"))
    save("wastes", dump_rows("waste_all.json"))
    save("goods", dump_rows("goods_all.json"))
    save("machines", dd_rows("dd_machine.json"))
    save("users", dd_rows("dd_all_user.json"))
    save("units", [{"id": 946731 + i, "name": n} for i, n in enumerate(["个", "kg", "PCS", "件", "米", "小时"])])
    save("workshops", [{"id": 823440, "name": "下料车间", "parent_id": None, "is_leaf": 1, "busi_type": 11,
                        "order_number": 1, "name_path": "下料车间", "del": 2},
                       {"id": 823441, "name": "切割组", "parent_id": 823440, "is_leaf": 1, "busi_type": 11,
                        "order_number": 2, "name_path": "下料车间/切割组", "del": 2}])
    save("produce_lines", [{"id": 946727, "name": "车削"}, {"id": 946730, "name": "磨削"}])
    print("sandbox fresh:", sorted(os.listdir(OUT)))
