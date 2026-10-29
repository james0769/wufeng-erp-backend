# -*- coding: utf-8 -*-
"""B12 企业配置域：enterprise_config（8 模块 get/save/batch 四端点）+ priority_configure（优先级字典）。
依据：deep\\29-设置页合集.md（§5.1 四端点契约 / §5.2 全 module 实测：有效 module 共 8 个 /
      §3.7 priority_configure 表结构：排序(拖拽)/名称(≤20字)/颜色(colorPicker 默认 #000000)）
      deep\\14-系统管理域.md（§6.2 module 收口 + work_order_type 带 order_type_id 维度特例）
实测样例：api_dump/ecfg_*.json（batch_a/b/d/enterprise/produce/work_order/mall/feedback，键值逐字内置）
          api_dump/deep_m21/ecfg_*_get.json（单模块 get 版，与 batch 版 data 全等）
          api_dump/deep_m6/ecfg_work_order_type1.json（work_order_type 维度样例）
          api_dump/deep_m14/priority_configure_list.json（加急145449/正常145450/延期145451）
数据落盘：backend/data/enterprise_configs.json（键=module；"_update_time" 为 meta 键，非 module）
          backend/data/priority_configures.json（行式，core.Store）
服务页面：生产设置 /setting/produce/dictionary、商品设置 /setting/goods、系统管理通用设置 /setting/basics
          （商品设置页的"goods_config"模块不存在——商品设置读写 enterprise_config，deep\\29 §0）
"""
import json

import core

STORE_NAME = "enterprise_configs"
UPDATE_TIME_SEED = "2026-10-28 00:49:20"    # 真实抓包的 enterprise_config_update_time（ecfg_batch_*.json 逐字）
PRIORITY_SEED_TIME = "2026-01-03 13:43:28"  # deep_m14/priority_configure_list.json 逐字
ENT_ID = 16807
PRIORITY_ID_BASE = 145451                   # 新增优先级 id 续接真实序列（deep_m14）

# ---------------- 8 个有效 module 默认键值（ecfg_*.json 实测原文逐字，deep\29 §5.2） ----------------
# enterprise_config 68 键 / produce_config 57 键 / mall_config 8 / work_order 26 / feedback 9 /
# new_work_order 14 / after_sale 1 / maintenance_config 18
DEFAULTS = {
    'enterprise_config': {
        'inventory_alarm': '{"roleIds":[],"deptIds":[823465],"userIds":[63097],"other":[]}',
        'map_hide_user_ids': '',
        'profile_push_time': '21:00',
        'allow_profile_push': True,
        'purchase_alert_days': 0,
        'goods_cost_money_type': 2,
        'is_first_in_first_out': False,
        'enable_photo_watermark': True,
        'sales_price_tract_rule': '[]',
        'sales_price_tract_type': 1,
        'allow_audit_status_hide': False,
        'allow_print_batch_merge': False,
        'has_choose_sale_modules': True,
        'supplier_register_token': 'xxx7yp',
        'enable_full_mobile_search': False,
        'purchase_price_tract_rule': '[]',
        'purchase_price_tract_type': 2,
        'allow_linkman_mobile_repeat': False,
        'contract_item_money_formula': '${合同明细.单价}*${合同明细.销售数量}',
        'contract_collection_settings': False,
        'report_fault_mobile_validate': False,
        'device_view_show_order_history': True,
        'is_contract_need_audit_setting': True,
        'single_view_show_order_history': True,
        'contract_item_money_decimal_num': 8,
        'goods_name_need_duplicate_check': False,
        'is_scan_goods_qrcode_need_login': False,
        'report_fault_can_select_linkman': True,
        'purchase_bill_item_money_formula': '${明细.采购单价}*${明细.采购数量}',
        'purchase_stock_in_bill_num_limit': False,
        'contract_stock_out_bill_num_limit': False,
        'stock_out_bill_item_money_formula': '${明细.销售单价}*${明细.销售数量}',
        'customer_name_need_duplicate_check': True,
        'enable_full_supplier_mobile_search': False,
        'goods_code_need_automatically_fill': True,
        'purchase_check_overdue_notice_days': 0,
        'supplier_name_need_duplicate_check': False,
        'device_view_show_inspection_history': True,
        'allow_supplier_linkman_mobile_repeat': False,
        'purchase_bill_item_money_decimal_num': 8,
        'sale_return_stock_item_money_formula': '${明细.销售单价}*${明细.退货数量}',
        'contract_quotation_bill_share_columns': '[{"title":"商品名","dataIndex":"goods_name"},{"title":"商品编号","dataIndex":"goods_code"},{"title":"类别","dataIndex":"goods_category_path_names"},{"title":"销售单价","dataIndex":"unit_price","type":"num_ipt"},{"title":"商品备注","dataIndex":"goods_remark"}]',
        'contract_quotation_item_money_formula': '${合同报价明细.单价}*${合同报价明细.销售数量}',
        'purchase_check_overdue_notice_is_open': False,
        'stock_out_bill_item_money_decimal_num': 8,
        'purchase_check_overdue_notice_settings': '{\\"roleIds\\":[],\\"deptIds\\":[],\\"userIds\\":[],\\"other\\":[1]}',
        'purchase_return_bill_item_money_formula': '${明细.采购单价}*${明细.退货数量}',
        'sale_return_stock_item_money_decimal_num': 8,
        'batch_purchase_quantity_default_supply_to': 50,
        'contract_quotation_item_money_decimal_num': 8,
        'purchase_stock_in_bill_item_money_formula': '${明细.采购单价}*${明细.采购数量}',
        'customer_contract_collection_date_reminder': 1,
        'purchase_return_bill_item_money_decimal_num': 8,
        'customer_contract_audit_pass_notice_user_ids': '63097,196798,233053,144814',
        'production_stock_out_bill_default_batch_name': '无批次',
        'purchase_stock_in_bill_item_money_decimal_num': 8,
        'is_automatic_calculate_customer_contract_money': True,
        'is_automatic_generation_customer_contract_code': True,
        'produce_stock_in_bill_num_big_produce_plan_num': False,
        'production_stock_out_bill_default_batch_enable': False,
        'customer_contract_collection_date_reminder_users': '{"roleIds":[],"deptIds":[],"userIds":[],"other":[1]}',
        'customer_contract_order_upcoming_overdue_reminder': 1,
        'customer_contract_order_upcoming_overdue_reminder_users': '{"roleIds":[],"deptIds":[],"userIds":[],"other":[1]}',
        'allow_show_supplier_linkman_and_address_of_purchase_bill': False,
        'allow_show_supplier_linkman_and_address_of_warehouse_bill': False,
        'allow_purchase_audit_status_hide': False,
        'is_automatic_calculate_purchase_bill_money': True,
        'bom_name_need_duplicate_check': False
    },
    'produce_config': {
        'produce_mode': 1,
        'allow_agent_report': True,
        'work_report_default': True,
        'produce_bill_overdue': '{"roleIds":[],"deptIds":[],"userIds":[],"other":[1]}',
        'lean_meeting_reminder': 5,
        'enable_lean_production': True,
        'limit_craft_report_num': False,
        'not_limit_agent_report': True,
        'produce_qr_is_single_qr': True,
        'production_task_overdue': '{"roleIds":[],"deptIds":[],"userIds":[],"other":[11]}',
        'limit_craft_produce_order': False,
        'work_in_process_craft_ids': '481203,481108,479649,479648,479647,479646,479645,478933,478931,478930,478929,478928,478927,478926,471884,471883,471882,470165,458623,458043,456220,456219,455446,455445,455444,455443,450438,450437,450436,446191,441814,432146,430982,430981,430980,429857,429608,429605,429604,429601,429590,429588,428522,427763,427762,427761,427760,427759,427758,427757',
        'craft_report_limit_formula': '${生产数量}',
        'produce_bill_craft_timeout': '{"roleIds":[],"deptIds":[],"userIds":[],"other":[2]}',
        'produce_bill_finish_remind': 0,
        'auto_complete_produce_craft': True,
        'produce_bill_add_in_produce': True,
        'according_to_sets_report_work': False,
        'lean_meeting_overdue_reminder': 0,
        'start_produce_date_in_produce': False,
        'craft_produce_order_limit_type': 1,
        'craft_start_produce_limit_type': 1,
        'no_reportable_user_is_all_user': True,
        'report_work_unit_price_formula': '',
        'auto_filling_child_produce_bill': True,
        'lean_production_daily_push_time': '21:00',
        'performance_pay_formula_content': 'case(${任务.计价方式}=="计件",${报工.良品数}*${报工.工资单价};\n${任务.计价方式}=="计时",${报工.报工时长}/3600*${报工.工资单价})',
        'enable_lean_production_daily_push': False,
        'single_page_is_show_produce_trace': True,
        'production_order_notification_time': '08:00',
        'is_automatic_generation_single_code': True,
        'performance_pay_formula_decimal_num': 0,
        'all_craft_finish_update_produce_status': True,
        'craft_report_limit_formula_decimal_num': 8,
        'craft_report_work_valid_num_limit_type': 1,
        'report_work_unit_price_formula_decimal': 8,
        'report_work_unit_price_formula_setting': False,
        'lean_production_salary_settlement_limit': False,
        'is_automatic_generation_produce_bill_code': True,
        'performance_pay_formula_is_format_decimal': 1,
        'lean_production_blacklist_display_data_num': '10',
        'start_produce_date_send_personnel_reminder': 2,
        'auto_change_produce_bill_priority_configure': True,
        'lean_production_blacklist_display_craft_ids': '366470,366475,366480,366479,366481,366482,366471,366472,366473,366474,366476,366478,366477,366504,366525,366526,366527,366528,366529,366530,366531,366547,366548,366549,366550,366565,366566,366567,366568,366569,366570,366574,366575,366576,366577,366578,366715,366717,366716,366718,366719,366720,366721,366722,366723,366735,366736,366737,366738,366740,366739,366741,366742,366743,366744,366745,367129,367130,367131,367132,367133,367353,367354,367481,367482,367483,368720,368721,368722,368723,368724,368725,368727,368726,368728,368729,368730,369001,369002,369003,369006,369007,369008,369009,369238',
        'lean_production_single_design_problem_alias': '设计问题',
        'mini_app_produce_craft_list_by_produce_line': True,
        'lean_production_goods_materiel_problem_alias': '物料问题',
        'lean_production_joint_responsibility_setting': True,
        'lean_production_single_design_problem_handler': 63097,
        'one_employee_multi_crafts_simultaneous_timing': True,
        'lean_production_goods_materiel_problem_handler': '171379,171381,171380,171383,171384,171385,171387,171386,63097',
        'lean_production_after_sale_fault_category_setting': '823476,823477',
        'lean_production_site_inspection_department_setting': '823465,823468,823471,823474',
        'mini_app_produce_craft_list_by_produce_line_fold_type': 1,
        'enable_start_produce_date_send_personnel_reminder': True,
        'allow_auto_start_next_craft': False,
        'team_report_work': True
    },
    'mall_config': {
        'online_pay': False,
        'offline_pay': False,
        'is_enable_mall': True,
        'offline_pay_alipay': '',
        'offline_pay_wechat': '',
        'display_sale_num_threshold': 50,
        'auto_complete_order_after_days': 3,
        'display_sale_num_on_detail_page': True
    },
    'work_order': {
        'allow_back': True,
        'finish_edit': True,
        'data_remind_time': 0,
        'enable_dated_sms': False,
        'pool_timeout_hour': 1,
        'enable_set_out_sms': False,
        'enable_map_dispatch': True,
        'enable_online_visit': True,
        'allow_handler_refuse': True,
        'pool_notice_distance': 0,
        'dont_allow_edit_steps': '6,7,8,9',
        'is_hide_customer_info': False,
        'allow_handler_transfer': True,
        'not_need_receive_again': True,
        'auto_receive_work_order': False,
        'allow_handler_edit_order': True,
        'is_export_work_order_img': False,
        'work_order_default_limit': 0,
        'is_enable_send_and_repair': False,
        'pool_enable_timeout_remind': True,
        'pool_max_notice_user_count': 0,
        'allow_handler_change_helpers': True,
        'is_use_spare_parts_related_goods': False,
        'user_work_order_unfinished_num_limit': 0,
        'work_order_total_timeout_alert_early': 0,
        'is_enabled_send_and_repair': False
    },
    'feedback': {
        'allow_back': True,
        'finish_edit': True,
        'enable_pause': True,
        'address_is_required': False,
        'allow_handler_refuse': True,
        'handle_timeout_limit': 1,
        'allow_handler_transfer': True,
        'not_need_receive_again': True,
        'allow_handler_change_helpers': True
    },
    'new_work_order': {
        'new_pool_timeout_hour': 0,
        'allow_handler_edit_order': True,
        'allow_handler_change_helpers': True,
        'finish_edit': True,
        'enable_map_dispatch': True,
        'is_hide_customer_info': False,
        'is_export_work_order_img': False,
        'user_work_order_unfinished_num_limit': 0,
        'supervision_user_ids': '',
        'date_remind_minutes': 30,
        'date_remind_user_rule': 1,
        'new_work_pool_overdue_reminder': '',
        'auto_dispatch_start_time': '00:00',
        'auto_dispatch_end_time': '23:59'
    },
    'after_sale': {
        'is_show_single_page_repair_button': True
    },
    'maintenance_config': {
        'device_order_is_need_audit': True,
        'is_export_device_order_img': False,
        'is_export_inspection_record_img': False,
        'device_status_is_it_based_on_work_order': False,
        'all_device_order_finish_device_status_is': None,
        'scan_qrcode_view_device_info_whether_login': False,
        'exists_not_finish_device_order_device_status_is': None,
        'inspection_device_hide_workshop_product_line_info': False,
        'maintenance_allow_select_device': False,
        'dispatch_type_by_scan_qrcode_view_device_info_whether_login': 2,
        'maintenance_task_timeout': '',
        'maintenance_task_missed_execution': '',
        'maintenance_records_abnormal': '',
        'maintenance_point_task_unexecuted_default_exec_result': 3,
        'repair_work_order_timeout': '',
        'repair_work_order_timeout_minutes': 0,
        'scan_qrcode_view_device_repair': True,
        'equipment_maintenance_allows_selection_all_spare_parts': False
    }
}

# work_order_type 特例：module 带 order_type_id 维度，返回键为 work_order_type_<id>（deep\14 §6.2）
WORK_ORDER_TYPE_DEFAULT = {
    'enable_pause': True,
    'date_limit': 1,
    'receive_limit': 1,
    'enable_auto_visit': True,
    'enable_manual_visit': True,
    'enable_manual_visit_sms_delay': True,
    'manual_visit_sms_delay_minutes': 1,
    'enable_finish_sign': True,
    'enable_single_product_order': True,
    'enable_phone_auto_visit': False,
    'enable_phone_manual_visit': False,
    'enable_date': True,
    'enable_audit': False,
    'enable_set_out': True,
    'enable_arrive': True,
    'commission_settle_type': 1,
    'commission_rate': 0,
    'commission_money': 0,
    'enable_change_goods_or_service_price': False,
    'lowest_modify_percentage': 80
}

KNOWN_MODULES = tuple(DEFAULTS.keys())

# ---------------- 配置库存取（内存单例 dict + 原子落盘） ----------------
_DB = None


def _db():
    global _DB
    if _DB is None:
        data = core._load(STORE_NAME, None)
        if not isinstance(data, dict) or not data:
            # 首次运行：整份默认值快照落盘（此后文件值覆盖代码默认）
            data = {"_update_time": UPDATE_TIME_SEED}
            for m, d in DEFAULTS.items():
                data[m] = dict(d)
        data.setdefault("_update_time", UPDATE_TIME_SEED)
        _DB = data
        _flush_db()
    return _DB


def _flush_db():
    core._save(STORE_NAME, _DB)


def _bump_update_time():
    _db()["_update_time"] = core.now()


def _net_error(with_time=False):
    """未知 module 的通用报错（deep\29 §5.1：单模块版无 update_time；batch 版错误体额外带）。"""
    r = core.fail("网络异常，请稍后重试", post_track=1)
    if with_time:
        r["enterprise_config_update_time"] = _db().get("_update_time")
    return r


def _merged(module):
    """代码内置默认值 + 落盘覆盖合并（后续默认值增键不影响已保存值）。"""
    out = dict(DEFAULTS[module])
    out.update(_db().get(module) or {})
    return out


def _parse_data(body):
    """save 载荷 data 兼容 JSON 串 / dict（原站两种形态：JSON.stringify(obj) / 对象直传）。"""
    raw = body.get("data")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    return raw if isinstance(raw, dict) else None


def _ot_key(body):
    return "work_order_type_%s" % (body.get("order_type_id") or 1)


# ---------------- enterprise_config 四端点（deep\29 §5.1，逐字形态） ----------------

def _get_by_module(body, session):
    module = str(body.get("module") or "").strip()
    if module == "work_order_type":
        key = _ot_key(body)
        db = _db()
        if key not in db:
            db[key] = dict(WORK_ORDER_TYPE_DEFAULT)
            _flush_db()
        return core.ok({key: db[key]})
    if module not in DEFAULTS:
        return _net_error()
    return core.ok({module: _merged(module)})


def _batch_get_by_module(body, session):
    arr = body.get("module_name_array") or []
    if isinstance(arr, str):
        arr = [arr]
    out = {}
    for m in arr:
        m = str(m or "").strip()
        if m == "work_order_type":
            key = _ot_key(body)
            db = _db()
            if key not in db:
                db[key] = dict(WORK_ORDER_TYPE_DEFAULT)
                _flush_db()
            out[key] = db[key]
            continue
        if m not in DEFAULTS:
            return _net_error(with_time=True)  # batch 含未知 module 整体报错（deep\29 §5.1）
        out[m] = _merged(m)
    res = core.ok(out)
    res["enterprise_config_update_time"] = _db().get("_update_time")
    return res


def _save_by_module(body, session):
    module = str(body.get("module") or "").strip()
    data = _parse_data(body)
    if data is None:
        return core.fail("参数解析失败")
    if not module:
        # 兼容形态一：{data: JSON.stringify({<module>:{...}})}（deep\14 §6.1 save_by_module 双形态）
        mods = [k for k in data if k in KNOWN_MODULES or str(k).startswith("work_order_type_")]
        if len(mods) != 1:
            return core.fail("参数错误")
        module = mods[0]
        data = data[module] if module in KNOWN_MODULES else {}
    db = _db()
    if module == "work_order_type":
        key = _ot_key(body)
        base = db.get(key) or dict(WORK_ORDER_TYPE_DEFAULT)
        base.update(data or {})
        db[key] = base
    elif module in DEFAULTS:
        db.setdefault(module, {}).update(data or {})
    else:
        return _net_error()
    _bump_update_time()
    _flush_db()
    return core.ok()


def _batch_save_by_module(body, session):
    """多模块一次保存：{data: JSON.stringify({模块:{键:值}})}——所有设置页主用（deep\29 §5.1）。"""
    payload = _parse_data(body)
    if payload is None:
        return core.fail("参数解析失败")
    db = _db()
    for module, kv in payload.items():
        if str(module).startswith("work_order_type_"):
            base = db.get(module) or dict(WORK_ORDER_TYPE_DEFAULT)
            base.update(kv or {})
            db[module] = base
        elif module in DEFAULTS:
            db.setdefault(module, {}).update(kv or {})
        # 未知 module 键忽略（前端载荷只含有效模块，deep\29 §5.2 代码侧引用统计）
    _bump_update_time()
    _flush_db()
    return core.ok()


# ---------------- priority_configure（优先级字典，deep\29 §3.7 + deep_m14 实测） ----------------
# 列：排序(拖拽 drag_move)/名称(≤20字)/颜色(colorPicker 默认 #000000)；type=1 生产优先级/2 服务工单。
# list 响应形态逐字对照 deep_m14/priority_configure_list.json：
#   {success, count, data:[...], code:0, enterprise_config_update_time}

_PC = None


def _pcs():
    global _PC
    if _PC is None:
        _PC = core.Store("priority_configures")
        if not _PC.rows:
            for i, (pid, name, color) in enumerate(
                    [(145449, "加急", "#ff1e02"), (145450, "正常", "#9aba60"), (145451, "延期", "#ffc12a")]):
                _PC.add({
                    "id": pid, "enterprise_id": ENT_ID, "name": name, "color": color,
                    "order_number": i + 1, "del": 2,
                    "created_at": PRIORITY_SEED_TIME, "updated_at": PRIORITY_SEED_TIME,
                    "type": 1, "fieldValueList": [],
                })
    return _PC


def _pc_ok(payload=None):
    res = core.ok(payload) if payload is not None else core.ok()
    res["enterprise_config_update_time"] = _db().get("_update_time")
    return res


def _pc_list(body, session):
    t = body.get("type")
    rows = [r for r in _pcs().all() if t in (None, "") or str(r.get("type")) == str(t)]
    rows = sorted(rows, key=lambda r: int(r.get("order_number") or 0))
    return {"success": True, "count": len(rows), "data": rows, "code": 0,
            "enterprise_config_update_time": _db().get("_update_time")}


def _pc_name_ok(val):
    name = str(val or "").strip()
    if not name:
        return None, core.fail("请输入名称")
    if len(name) > 20:
        return None, core.fail("名称不能超过20个字")
    return name, None


def _pc_rows_of(ptype):
    rows = [r for r in _pcs().all() if str(r.get("type")) == str(ptype)]
    return sorted(rows, key=lambda r: int(r.get("order_number") or 0))


def _apply_order(rows):
    for i, r in enumerate(rows):
        r["order_number"] = i + 1
    _pcs().flush()


def _pc_add(body, session):
    name, err = _pc_name_ok(body.get("name"))
    if err:
        return err
    mx = PRIORITY_ID_BASE
    for r in _pcs().all():
        try:
            mx = max(mx, int(r.get("id")))
        except (TypeError, ValueError):
            pass
    row = _pcs().add({
        "id": mx + 1, "enterprise_id": session.get("enterprise_id") or ENT_ID,
        "name": name, "color": body.get("color") or "#000000",
        "order_number": len(_pc_rows_of(body.get("type") or 1)) + 1, "del": 2,
        "type": body.get("type") or 1, "fieldValueList": [],
    })
    return _pc_ok(row)


def _pc_edit(body, session):
    row = _pcs().get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    patch = {}
    if "name" in body:
        name, err = _pc_name_ok(body.get("name"))
        if err:
            return err
        patch["name"] = name
    if "color" in body:
        patch["color"] = body.get("color") or "#000000"
    _pcs().update(row["id"], patch)
    return _pc_ok()


def _pc_del(body, session):
    row = _pcs().get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    _pcs().remove(row["id"])
    _apply_order(_pc_rows_of(row.get("type")))
    return _pc_ok()


def _pc_move(body, session):
    """上移/下移：{id, move_type:1上移/2下移}（direction 别名兼容）。"""
    row = _pcs().get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    direction = str(body.get("move_type") or body.get("direction") or "1")
    rows = _pc_rows_of(row.get("type"))
    idx = next((i for i, r in enumerate(rows) if str(r.get("id")) == str(row["id"])), -1)
    if idx < 0:
        return core.fail("数据不存在")
    if direction in ("2", "down") and idx < len(rows) - 1:
        rows[idx], rows[idx + 1] = rows[idx + 1], rows[idx]
    elif direction in ("1", "up") and idx > 0:
        rows[idx], rows[idx - 1] = rows[idx - 1], rows[idx]
    _apply_order(rows)
    return _pc_ok()


def _pc_drag_move(body, session):
    """拖拽排序：{id, order_number}，插入目标位后顺序重排（deep\29 §3.7 drag_move{id, order_number}）。"""
    row = _pcs().get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    try:
        target = int(body.get("order_number") or 1)
    except (TypeError, ValueError):
        return core.fail("参数错误")
    rows = [r for r in _pc_rows_of(row.get("type")) if str(r.get("id")) != str(row["id"])]
    target = max(1, min(target, len(rows) + 1))
    rows.insert(target - 1, row)
    _apply_order(rows)
    return _pc_ok()


# ---------------- 启动种子（server 导入即落盘两个种子文件） ----------------
_db()
_pcs()

ROUTES = {
    # enterprise_config 四端点（真实名）+ 通用别名（get/save/batch_get/batch_save）
    ("enterprise_config", "get_by_module"): _get_by_module,
    ("enterprise_config", "get"): _get_by_module,
    ("enterprise_config", "save_by_module"): _save_by_module,
    ("enterprise_config", "save"): _save_by_module,
    ("enterprise_config", "batch_get_by_module"): _batch_get_by_module,
    ("enterprise_config", "batch_get"): _batch_get_by_module,
    ("enterprise_config", "batch_save_by_module"): _batch_save_by_module,
    ("enterprise_config", "batch_save"): _batch_save_by_module,
    # priority_configure（加工单优先级编辑器）
    ("priority_configure", "list"): _pc_list,
    ("priority_configure", "add"): _pc_add,
    ("priority_configure", "edit"): _pc_edit,
    ("priority_configure", "del"): _pc_del,
    ("priority_configure", "move"): _pc_move,
    ("priority_configure", "drag_move"): _pc_drag_move,
}
