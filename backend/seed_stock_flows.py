# -*- coding: utf-8 -*-
"""B6 配套 · 仓库域出入库单/流水种子生成器（独立于 seed.py，可重复运行）。

数据来源：backend/data/produce_bills.json（431 行加工单种子，真实导出口径）。
产出（均带 {"_seed":1,"_seed_src":"seed_stock_flows"} 标记，与真实抓包行可区分）：
  1) data/stock_in_bills.json   成品入库单（stock_in_bill/list 数据源，Store 行结构）
      - 口径：状态∈{已完成,生产中} 且 有 stock_in_bill_num（含多号拆行；真实前缀 CPRK=成品入库 191 /
        SCRK=半成品入库 9，SCRK 行 stock_type=966270、流水 bill_type=21）；
        已完成但无单号的补造 CPRK+YYYYMMDD+3位（deep12 §11.5 编号规则）。
      - 行 type=2（本复刻 stock_in_bills Store 合并入库单，列表默认 type=2 可见），
        成品入库语义由 stock_type_id=950911(成品入库) + CPRK 前缀承载。
  2) data/stock_out_bills.json  生产领料出库单（stock_out_bill/list 数据源）
      - 口径：有 stock_out_bill_num 的加工单（≈全部领料/部分领料，多号拆行）。
      - 行 type=3（STOCK_OUT_BILL_TYPE.PRODUCTION_STOCK_OUT，同模块
        production_stock_out_bill_add 写入口径），stock_type_id=950855(生产出库)+SCCK 前缀；
        另按"完工尾料清理"故事补少量 type=2 其他出库（QTCK），使默认 type=2 列表亦有数据。
  3) data/stock_items.json      出入库流水（stock_item/list 数据源；追加式，保留 probes 真实行）
      - 每张入库/出库单明细一行；bill_type 按 deep12 §4.2 十六种单据类型枚举标注：
        1=生产入库单 / 21=半成品入库单 / 12=生产出库单 / 5=其他出库单 / 17=生产退料单(取消单退料) / 6=库存调拨单(调拨)。
      - 流水编号=企业id+YYYYMMDD+3位（同 _write_flow/core.next_code 口径），按日递增且避开已有真实编号；
        结余(in/out_stock_balance_num)=按 (商品,仓) 时间序累计，并平移使末行结余=goods_stocks 现存量。

仓库映射：成品入库→成品库(27210，probes 实测 in_ware_id)  生产领料/退料→原料库(27211)
          尾料清理→尾料库(27212)  调拨=成品库→精工仓储(26950)。
幂等：默认已生成（stock_in_bills 含本脚本标记行）则跳过；--force 先剥离旧标记行再重建。
不修改 seed.py 与其它种子文件；stock_items.json 仅追加/替换本脚本标记行。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
MARK = {"_seed": 1, "_seed_src": "seed_stock_flows"}

ENT_ID = 16807
UID_DEFAULT = 63097                     # 小王（probes 实测操作人）
WARE_FINISHED = 27210                   # 成品库
WARE_RAW = 27211                        # 原料库
WARE_TAIL = 27212                       # 尾料库
WARE_JG = 26950                         # 精工仓储
ST_IN_FINISHED = 950911                 # 成品入库（IN_WARE_TYPE）
ST_IN_RETURN = 950913                   # 退料 入库
ST_OUT_PRODUCE = 950855                 # 生产出库（OUT_WARE_TYPE）
ST_OUT_ELSE = 946702                    # 普通出库

# deep12 §4.2 十六种单据类型（本脚本用到的子集）
BT_PRODUCE_IN = 1        # 生产入库单（produce_stock_in_bill）
BT_ELSE_OUT = 5          # 其他出库单（stock_out_bill）
BT_ALLOCATE = 6          # 库存调拨单（stock_allocate_bill）
BT_PRODUCE_OUT = 12      # 生产出库单（stock_out_bill）
BT_RETURN = 17           # 生产退料单（produce_return_materiel_bill）
BT_SEMI_IN = 21          # 半成品入库单（semi_finished_stock_in_bill）

IN_PCT = re.compile(r"^(\d+)%\((\d+)/(\d+)\)")
NUM_DATE = re.compile(r"^(CPRK|SCCK|SCRK)(\d{8})(\d{3})$")
SPLIT_RE = re.compile(r"[，,、/]+")


def f8(x):
    try:
        v = round(float(x), 8)
    except (TypeError, ValueError):
        v = 0.0
    if abs(v) < 1e-9:
        v = 0.0
    return "%.8f" % v


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def load(name):
    with open(os.path.join(DATA_DIR, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def save(name, rows):
    with open(os.path.join(DATA_DIR, name + ".json"), "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)


def _time_seq(i):
    """确定性流水时间：避免随机因子，保证重跑结果一致。"""
    return "%02d:%02d:%02d" % (8 + i % 9, i % 60, (i * 7) % 60)


def split_numbers(raw):
    """'CPRK20261015001，CPRK20261015002' → ['CPRK20261015001', ...]"""
    return [p.strip() for p in SPLIT_RE.split(str(raw or "")) if p.strip()]


def split_qty(total, k):
    """把 total 均拆 k 份（前置份取整，末份兜余量；total>=k 时每份>=1）。"""
    total = int(max(1, round(total)))
    k = max(1, k)
    if total < k:
        return [1] * total + [0] * (k - total)
    base = total // k
    return [base] * (k - 1) + [total - base * (k - 1)]


def goods_mini(g):
    return {"id": g.get("id"), "code": g.get("code"), "name": g.get("name"),
            "standard": g.get("standard"), "jianpin": g.get("jianpin"),
            "unit": g.get("unit"), "source": g.get("source"),
            "category_id": g.get("category_id"),
            "category_path_names": g.get("category_path_names"),
            "category_path_ids": g.get("category_path_ids"),
            "cost_money": g.get("cost_money"), "selling_money": g.get("selling_money"),
            "avatars": []}


def make_item(goods, qty):
    cp = _f(goods.get("cost_money"))
    sp = _f(goods.get("selling_money"))
    return {"goods_id": goods.get("id"), "goods": goods_mini(goods), "num": float(qty),
            "cost_price": f8(cp), "selling_price": f8(sp),
            "cost_money": f8(qty * cp), "selling_money": f8(qty * sp),
            "remark": None, "batch_name": "无批次"}


class Seq:
    """按日前缀分配 3 位序号，避开已有编号（真实行 + 本批次）。"""

    def __init__(self, prefix_len, used):
        self.prefix_len = prefix_len
        self.used = set(used)
        self.next = {}

    def take(self, code):
        day = code[self.prefix_len:self.prefix_len + 8]
        n = self.next.get(day, 1)
        while ("%s%s%s" % (code[:self.prefix_len], day, str(n).zfill(3))) in self.used:
            n += 1
        self.next[day] = n + 1
        return code[:self.prefix_len] + day + str(n).zfill(3)


def build(force=False):
    produce = load("produce_bills")
    goods_by_code = {g.get("code"): g for g in load("goods")}
    users = load("users")
    by_real = {u.get("real_name"): u.get("id") for u in users}
    by_mobile = {u.get("mobile"): u.get("id") for u in users}

    def uid_of(name):
        return by_real.get(name) or by_mobile.get(name) or UID_DEFAULT

    gs_rows = load("goods_stocks")
    gs_num = {(str(r.get("goods_id")), str(r.get("ware_id"))): _f(r.get("num")) for r in gs_rows}

    # bom 树：能取到子料用子料（bom_trees 仅 10 商品有实测树），否则领料商品自身
    trees = load("bom_trees") if os.path.exists(os.path.join(DATA_DIR, "bom_trees.json")) else []
    tree_by_goods = {t.get("goods_id"): t for t in trees}

    def materials_of(bill, goods):
        tree = tree_by_goods.get(goods.get("id"))
        out = []
        for node in (tree or {}).get("structure_tree") or []:
            m = node.get("goods") or {}
            if m.get("id"):
                out.append((m, _f(node.get("num")) or 1.0))
        return out or [(goods, 1.0)]

    # ---------- 现有文件与幂等 ----------
    in_path = os.path.join(DATA_DIR, "stock_in_bills.json")
    out_path = os.path.join(DATA_DIR, "stock_out_bills.json")
    si_path = os.path.join(DATA_DIR, "stock_items.json")
    old_in = load("stock_in_bills") if os.path.exists(in_path) else []
    old_out = load("stock_out_bills") if os.path.exists(out_path) else []
    old_si = load("stock_items") if os.path.exists(si_path) else []
    mine_in = [r for r in old_in if r.get("_seed_src") == MARK["_seed_src"]]
    mine_out = [r for r in old_out if r.get("_seed_src") == MARK["_seed_src"]]
    if (mine_in or mine_out) and not force:
        print("[seed_stock_flows] 已生成（in=%d out=%d），跳过；--force 可重建" % (len(mine_in), len(mine_out)))
        return

    # 真实编号集合（补造编号需避开）
    real_codes = set()
    for r in produce:
        real_codes.update(split_numbers(r.get("stock_in_bill_num")))
        real_codes.update(split_numbers(r.get("stock_out_bill_num")))

    # ---------- a) 成品入库单 ----------
    in_rows, in_done, synth_in = [], 0, 0
    for bill in produce:
        if bill.get("status") not in (2, 3):            # 生产中 / 已完成
            continue
        nums = split_numbers(bill.get("stock_in_bill_num"))
        m = IN_PCT.match(str(bill.get("stock_in_percent") or ""))
        done = int(m.group(2)) if m else 0
        if not nums and bill.get("status") != 3:
            continue                                     # 生产中未入库：不造
        if done <= 0:                                    # 已完成未回写：按实际生产数补
            done = int(_f(bill.get("actual_num")) or _f(bill.get("num")) or 1)
        goods = goods_by_code.get(bill.get("goods_code"))
        if not goods:
            continue
        qty = split_qty(done, len(nums)) if nums else [done]
        seq = Seq(4, real_codes)
        for i, num in enumerate(nums or [None]):
            if num:
                md = NUM_DATE.match(num)
                date = "%s-%s-%s" % (md.group(2)[:4], md.group(2)[4:6], md.group(2)[6:]) if md else str(bill.get("created_at"))[:10]
                code = num
            else:                                        # 补造 CPRK+日期+3位
                date = str(bill.get("end_time") or bill.get("created_at"))[:10]
                code = seq.take("CPRK" + date.replace("-", ""))
                real_codes.add(code)
                synth_in += 1
            if qty[i] <= 0:
                continue
            in_done += 1
            # SCRK=半成品入库（stock_type 966270 / 流水 bill_type=21），CPRK/补造=成品入库
            semi = bool(num) and num.startswith("SCRK")
            in_rows.append(dict(MARK, **{
                "id": 91000000 + in_done, "enterprise_id": ENT_ID, "type": 2,
                "code": code, "stock_type_id": 966270 if semi else ST_IN_FINISHED,
                "ware_id": WARE_FINISHED,
                "bill_date": date, "operation_type": 2,
                "produce_bill_id": bill.get("id"), "produce_bill_code": bill.get("code"),
                "supplier_id": None, "consignee_id": uid_of(bill.get("create_user_name")),
                "remark": "%s（加工单 %s）" % ("半成品入库" if semi else "成品入库", bill.get("code")),
                "audit_status": 1, "del": 2,
                "item_list": [make_item(goods, qty[i])],
                "create_user_id": uid_of(bill.get("create_user_name")),
                "created_at": "%s %s" % (date, _time_seq(in_done)),
                "updated_at": "%s %s" % (date, _time_seq(in_done)),
            }))

    # ---------- b) 生产领料出库单 ----------
    out_rows, out_done = [], 0
    for bill in produce:
        nums = split_numbers(bill.get("stock_out_bill_num"))
        if not nums:
            continue
        goods = goods_by_code.get(bill.get("goods_code"))
        if not goods:
            continue
        mats = materials_of(bill, goods)
        for num in nums:
            md = NUM_DATE.match(num)
            date = ("%s-%s-%s" % (md.group(2)[:4], md.group(2)[4:6], md.group(2)[6:])) if md \
                else str(bill.get("start_time") or bill.get("created_at"))[:10]
            out_done += 1
            items = [make_item(m, max(1.0, round(_f(bill.get("num")) * per / len(nums))))
                     for m, per in mats]
            out_rows.append(dict(MARK, **{
                "id": 92000000 + out_done, "enterprise_id": ENT_ID, "type": 3,
                "production_stock_out_type": 1, "operation_type": 2,
                "code": num, "stock_type_id": ST_OUT_PRODUCE, "ware_id": WARE_RAW,
                "bill_date": date, "produce_bill_id": bill.get("id"),
                "produce_bill_craft_id": None, "report_work_record_id": None,
                "shipper_id": uid_of(bill.get("create_user_name")),
                "supplier_id": None, "purchase_bill_id": None,
                "remark": "生产领料出库（加工单 %s）" % bill.get("code"),
                "audit_status": 1, "del": 2, "item_list": items,
                "create_user_id": uid_of(bill.get("create_user_name")),
                "created_at": "%s %s" % (date, _time_seq(out_done)),
                "updated_at": "%s %s" % (date, _time_seq(out_done)),
            }))

    # 其他出库（QTCK）：完工尾料清理故事 → 尾料库，填实默认 type=2 列表
    finished = [b for b in produce if b.get("status") == 3]
    seq_qt = Seq(4, real_codes)
    for i, bill in enumerate(finished[::10]):
        goods = goods_by_code.get(bill.get("goods_code"))
        if not goods:
            continue
        date = str(bill.get("end_time") or bill.get("created_at"))[:10]
        code = seq_qt.take("QTCK" + date.replace("-", ""))
        out_done += 1
        out_rows.append(dict(MARK, **{
            "id": 92000000 + out_done, "enterprise_id": ENT_ID, "type": 2,
            "code": code, "stock_type_id": ST_OUT_ELSE, "ware_id": WARE_TAIL,
            "bill_date": date, "produce_bill_id": None,
            "shipper_id": uid_of(bill.get("create_user_name")), "supplier_id": None,
            "remark": "完工尾料清理出库（加工单 %s）" % bill.get("code"),
            "audit_status": 1, "del": 2, "item_list": [make_item(goods, 1)],
            "create_user_id": uid_of(bill.get("create_user_name")),
            "created_at": "%s %s" % (date, _time_seq(out_done)),
            "updated_at": "%s %s" % (date, _time_seq(out_done)),
        }))

    # ---------- c) 出入库流水 ----------
    flows = []

    def add_flow(**kw):
        flows.append(kw)

    for r in in_rows:
        semi = str(r["code"]).startswith("SCRK")
        bt = BT_SEMI_IN if semi else BT_PRODUCE_IN
        for it in r["item_list"]:
            add_flow(direction=1, bill_type=bt, ware_in=r["ware_id"], ware_out=None,
                     stock_type_id=r["stock_type_id"], bill_id=r["id"], bill_code=r["code"],
                     goods_id=it["goods_id"], item=it, bill_date=r["bill_date"],
                     operator_id=r["create_user_id"], sort=r["created_at"])
    for r in out_rows:
        bt = BT_PRODUCE_OUT if r["type"] == 3 else BT_ELSE_OUT
        st = ST_OUT_PRODUCE if r["type"] == 3 else ST_OUT_ELSE
        for it in r["item_list"]:
            add_flow(direction=2, bill_type=bt, ware_in=None, ware_out=r["ware_id"],
                     stock_type_id=st, bill_id=r["id"], bill_code=r["code"],
                     goods_id=it["goods_id"], item=it, bill_date=r["bill_date"],
                     operator_id=r["create_user_id"], sort=r["created_at"])

    # 生产退料（bill_type=17）：已取消但已领料的加工单 → 子料/自身退回原料库（前缀 SCTL 为推断，未捕获实测前缀）
    for bill in produce:
        if bill.get("status") == 4 and bill.get("stock_out_bill_num"):
            goods = goods_by_code.get(bill.get("goods_code"))
            if not goods:
                continue
            date = str(bill.get("end_time") or bill.get("created_at"))[:10]
            add_flow(direction=1, bill_type=BT_RETURN, ware_in=WARE_RAW, ware_out=None,
                     stock_type_id=ST_IN_RETURN, bill_id=None,
                     bill_code="SCTL" + date.replace("-", "") + "001",
                     goods_id=goods.get("id"), item=make_item(goods, 1), bill_date=date,
                     operator_id=uid_of(bill.get("create_user_name")),
                     sort="%s 23:00:00" % date)

    # 库存调拨（bill_type=6，direction=3）：成品库 → 精工仓储（KCDB 前缀，deep12 §6 实测）
    for i, bill in enumerate(finished[3:24:5]):
        goods = goods_by_code.get(bill.get("goods_code"))
        if not goods:
            continue
        date = str(bill.get("end_time") or bill.get("created_at"))[:10]
        add_flow(direction=3, bill_type=BT_ALLOCATE, ware_in=WARE_JG, ware_out=WARE_FINISHED,
                 stock_type_id=None, bill_id=None,
                 bill_code="KCDB" + date.replace("-", "") + "001",
                 goods_id=goods.get("id"), item=make_item(goods, 1), bill_date=date,
                 operator_id=uid_of(bill.get("create_user_name")),
                 sort="%s 22:00:00" % date)

    # 排序 + 编号 + 结余（期初 = 现存 − 净流量 → 期末恰好回到 goods_stocks 现存量）
    flows.sort(key=lambda f: (f["sort"], str(f["bill_code"])))
    used_codes = {str(r.get("code")) for r in old_si if not r.get("_seed_src")}
    seq_flow = Seq(5, used_codes)
    net_flow = {}
    for f in flows:
        gid, num = str(f["goods_id"]), _f(f["item"]["num"])
        if f["ware_out"] is not None:
            k = (gid, str(f["ware_out"]))
            net_flow[k] = net_flow.get(k, 0.0) - num
        if f["ware_in"] is not None:
            k = (gid, str(f["ware_in"]))
            net_flow[k] = net_flow.get(k, 0.0) + num
    bal = {k: gs_num.get(k, 0.0) - v for k, v in net_flow.items()}
    si_rows = []
    for i, f in enumerate(flows):
        day = f["bill_date"].replace("-", "")
        code = seq_flow.take(str(ENT_ID) + day)
        gid, wi, wo = str(f["goods_id"]), f["ware_in"], f["ware_out"]
        num = _f(f["item"]["num"])
        if wo is not None:
            bal[(gid, str(wo))] = bal.get((gid, str(wo)), 0.0) - num
        if wi is not None:
            bal[(gid, str(wi))] = bal.get((gid, str(wi)), 0.0) + num
        if f["direction"] == 3:
            in_bal, out_bal = bal[(gid, str(wi))], bal[(gid, str(wo))]
        else:
            in_bal = bal[(gid, str(wi))] if f["direction"] == 1 else None
            out_bal = bal[(gid, str(wo))] if f["direction"] == 2 else None
        it = f["item"]
        si_rows.append(dict(MARK, **{
            "id": 93000000 + i + 1, "enterprise_id": ENT_ID, "code": code,
            "goods_id": f["goods_id"], "batch_name": "无批次",
            "in_ware_id": wi, "out_ware_id": wo, "operator_id": f["operator_id"],
            "stock_type_id": f["stock_type_id"], "bill_id": f["bill_id"],
            "bill_type": f["bill_type"], "direction": f["direction"],
            "num": f8(num), "waste_num": "0.00000000",
            "selling_price": it["selling_price"], "selling_money": it["selling_money"],
            "cost_price": it["cost_price"], "cost_money": it["cost_money"],
            "remark": None, "bill_date": f["bill_date"],
            "received_money": "0.00000000", "return_money": "0.00000000",
            "in_stock_balance_num": f8(in_bal) if in_bal is not None else None,
            "out_stock_balance_num": f8(out_bal) if out_bal is not None else None,
            "sum_num": f8(num), "del": 2,
            "bill": {"id": f["bill_id"], "code": f["bill_code"]},
            "created_at": f["sort"], "updated_at": f["sort"],
        }))

    # ---------- 写盘（保留非本脚本行；新行在前，模仿 Store.add 最新在前） ----------
    def newest_first(rows):
        return sorted(rows, key=lambda r: (str(r.get("created_at") or ""), int(r.get("id") or 0)), reverse=True)

    keep_in = [r for r in old_in if r.get("_seed_src") != MARK["_seed_src"]]
    keep_out = [r for r in old_out if r.get("_seed_src") != MARK["_seed_src"]]
    keep_si = [r for r in old_si if r.get("_seed_src") != MARK["_seed_src"]]
    save("stock_in_bills", newest_first(keep_in + in_rows))
    save("stock_out_bills", newest_first(keep_out + out_rows))
    save("stock_items", newest_first(keep_si + si_rows))

    bt_count = {}
    for r in si_rows:
        bt_count[r["bill_type"]] = bt_count.get(r["bill_type"], 0) + 1
    cnt = lambda rows, p: sum(1 for r in rows if str(r["code"]).startswith(p))
    print("[seed_stock_flows] in_bills=%d（真实CPRK %d / 真实SCRK %d / 补造CPRK %d） out_bills=%d（SCCK生产出库 %d / QTCK其他出库 %d）"
          % (len(in_rows), cnt(in_rows, "CPRK") - synth_in, cnt(in_rows, "SCRK"), synth_in,
             len(out_rows), cnt(out_rows, "SCCK"), cnt(out_rows, "QTCK")))
    print("[seed_stock_flows] stock_items 追加 %d 行（保留真实行 %d）；bill_type 分布 %s"
          % (len(si_rows), len(keep_si), json.dumps(bt_count, ensure_ascii=False)))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    build(force="--force" in sys.argv)
