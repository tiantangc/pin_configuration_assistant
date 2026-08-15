#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
引脚配置辅助助手 —— 本地网页版（M1.5 里程碑）
技术栈：NiceGUI
运行：
    pip install nicegui
    python app.py
    浏览器打开 http://127.0.0.1:8080
"""
import html as html_mod
from typing import Any, Dict, List, Tuple

from nicegui import ui

from solver import (Chip, Solver, build_requests_from_spec,
                    candidate_options_for_request, diagnose_no_solution,
                    format_solution, list_peripherals, load_chip,
                    parse_lock_text)

chip: Chip = load_chip("STM32F103C8Tx")
all_periphs: List[Dict[str, Any]] = sorted(
    list_peripherals(), key=lambda p: (p.get("name", ""), p.get("id", "")))
periph_options = {p["id"]: f"{p.get('icon','')} {p['name']} —— {p.get('description','')}"
                 for p in all_periphs}

# 哪些外设模板是 I2C 类（需要显示共享组选择）
I2C_PERIPH_IDS = {
    p["id"] for p in all_periphs
    if any(r.get("type") == "i2c" for r in p.get("requests", []))
}

# I2C 共享组选项：相同组名强制共用一条总线，不同组名强制分开
BUS_GROUP_OPTIONS = {
    "auto": "自动（求解器按省脚原则决定）",
    "A": "共享组 A",
    "B": "共享组 B",
    "C": "共享组 C",
    "D": "共享组 D",
}

# 默认场景 = 你的痛点场景
DEFAULT_SCENARIO_SPEC: List[Tuple[str, int]] = [
    ("i2c_screen", 1),
    ("mpu6050", 1),
    ("openmv", 1),
    ("stepper_motor", 3),
    ("car_motor", 2),
    ("encoder_hw", 1),
    ("encoder_gpio", 1),
]

rows: List[Dict[str, Any]] = []
rows_container = None
reserve_rows: List[Dict[str, Any]] = []
reserve_rows_container = None
result_html = None

# 引脚下拉选项：引脚名 + 默认功能备注
pin_options = {}
for _pin, _info in chip.pins.items():
    _notes = "、".join(_info.get("notes", []))
    pin_options[_pin] = _pin + (f"（{_notes}）" if _notes else "")


def solution_to_html(chip: Chip, sol, idx: int) -> str:
    """把方案文本转成 HTML，保留等宽对齐格式。"""
    text = format_solution(chip, sol, idx)
    return ('<pre style="background:#f6f8fa;padding:12px;border-radius:8px;'
            'overflow-x:auto;font-size:13px;line-height:1.45">'
            + html_mod.escape(text) + "</pre>")


def build_spec() -> List[Tuple]:
    """按页面上每一行收集 (外设id, 数量, I2C共享组, 锁定文本)。"""
    spec: List[Tuple] = []
    for r in rows:
        pid = r["select"].value
        try:
            cnt = int(r["count"].value or 0)
        except Exception:
            cnt = 0
        if pid and cnt > 0:
            if pid in I2C_PERIPH_IDS:
                bg_raw = r["share_sel"].value
                bg = None if bg_raw == "auto" else bg_raw
            else:
                bg = None
            lock_text = (r.get("lock_text") or "").strip()
            spec.append((pid, cnt, bg, lock_text))
    return spec


def add_row(pid: str = None, count: int = 1, bus_group: str = "auto") -> None:
    """在页面上添加一行外设选择。I2C 类外设会额外显示共享组选择。"""
    if pid is None:
        pid = all_periphs[0]["id"]
    with rows_container:
        with ui.row().classes("items-center gap-2") as row:
            sel = ui.select(periph_options, value=pid).classes("w-full max-w-xl")
            share_sel = ui.select(BUS_GROUP_OPTIONS, value=bus_group).classes("w-64")
            share_sel.set_visibility(pid in I2C_PERIPH_IDS)
            sel.on_value_change(lambda e: share_sel.set_visibility(e.value in I2C_PERIPH_IDS))
            cnt = ui.number("数量", value=count, min=0, max=16, format="%.0f").classes("w-24")
            lock_btn = ui.button("🔓 未锁定", on_click=None).props("flat color=blue")
            delete_btn = ui.button("✖").props("flat dense color=red")
        # 锁定设置对话框
        with ui.dialog() as lock_dialog, ui.card().classes("w-96"):
            lock_panel = ui.column().classes("w-full gap-2")
    row_data = {"row": row, "select": sel, "share_sel": share_sel,
                "count": cnt, "lock_btn": lock_btn, "delete_btn": delete_btn,
                "lock_dialog": lock_dialog, "lock_panel": lock_panel,
                "lock_text": ""}
    lock_btn.on_click(lambda: open_lock_dialog(row_data))
    delete_btn.on_click(lambda: delete_row(row_data))
    rows.append(row_data)


def current_reserved_pins() -> set:
    """当前页面所有保留引脚。"""
    return {r["select"].value for r in reserve_rows if r["select"].value}


def open_lock_dialog(row_data: Dict[str, Any]) -> None:
    """打开锁定设置对话框：为每个请求生成合法候选下拉框。

    - 其他行已锁定的引脚会从选项中排除
    - 本行之前保存的锁定会回显
    """
    pid = row_data["select"].value
    try:
        cnt = int(row_data["count"].value or 0)
    except Exception:
        cnt = 0
    if pid is None or cnt <= 0:
        return

    if pid in I2C_PERIPH_IDS:
        bg_raw = row_data["share_sel"].value
        bg = None if bg_raw == "auto" else bg_raw
    else:
        bg = None

    try:
        reqs = build_requests_from_spec([(pid, cnt, bg, "")])
        # 用旧锁定文本重新构建一次，得到每个请求原来锁定的引脚（用于回显）
        reqs_old = build_requests_from_spec([(pid, cnt, bg, row_data.get("lock_text") or "")])
    except ValueError:
        # 旧锁定若解析失败（如引脚已不再合法），忽略回显，只构建干净请求
        reqs = build_requests_from_spec([(pid, cnt, bg, "")])
        reqs_old = reqs

    # 其他行已锁定的引脚，本行选项里排除
    used_locked_pins: set = set()
    for r in rows:
        if r is row_data:
            continue
        for _, pin in parse_lock_text(r.get("lock_text") or ""):
            if pin.upper() != "AUTO":
                used_locked_pins.add(pin)

    panel = row_data["lock_panel"]
    panel.clear()
    reserved = current_reserved_pins()
    lock_controls: List[Dict[str, Any]] = []

    with panel:
        ui.label(f"锁定设置：{pid} × {cnt}").classes("font-bold")
        ui.markdown("下拉框只列出**该角色合法且未被占用**的候选，选「自动」表示不锁定。").classes("text-xs text-gray-500")

        for req, req_old in zip(reqs, reqs_old):
            old_pins = list(req_old.locked_pins or [])
            if req.kind == "exti_gpio" and req.count == 2:
                exti_pins = [p for p in chip.exti_candidates(reserved)
                             if p not in used_locked_pins]
                opts = {"auto": "自动（不锁定）"}
                opts.update({p: p for p in exti_pins})
                ui.label(f"{req.periph_name} {req.role}（A/B 相）").classes("font-medium")
                with ui.row().classes("gap-2"):
                    sel_a = ui.select(opts, value=old_pins[0] if len(old_pins) >= 1 and old_pins[0] in opts else "auto").classes("w-40")
                    sel_b = ui.select(opts, value=old_pins[1] if len(old_pins) >= 2 and old_pins[1] in opts else "auto").classes("w-40")
                lock_controls.append({"kind": "exti_pair", "req": req,
                                      "sel_a": sel_a, "sel_b": sel_b})
            else:
                opts = candidate_options_for_request(chip, req, reserved,
                                                     blocked=used_locked_pins)
                if not opts:
                    ui.label(f"{req.periph_name} {req.role}：无可锁定候选（可能引脚已被保留或锁定）").classes("text-xs text-red-500")
                    continue
                label_to_pins = {label: pins for label, pins in opts}
                options = {"auto": "自动（不锁定）"}
                options.update({label: label for label, _ in opts})
                ui.label(f"{req.periph_name} {req.role}").classes("font-medium")
                # 回显：旧 locked_pins 匹配某个候选则选中
                default_val = "auto"
                if old_pins:
                    for label, pins in opts:
                        if pins == old_pins:
                            default_val = label
                            break
                sel = ui.select(options, value=default_val).classes("w-full")
                lock_controls.append({"kind": "normal", "req": req, "sel": sel,
                                      "label_to_pins": label_to_pins,
                                      "pin_count": len(opts[0][1]) if opts else 1})

        with ui.row().classes("gap-2 mt-2"):
            ui.button("保存锁定", on_click=lambda: save_locks(row_data, lock_controls)).props("color=blue")
            ui.button("取消", on_click=lambda: row_data["lock_dialog"].close()).props("flat")

    row_data["lock_dialog"].open()


def save_locks(row_data: Dict[str, Any], lock_controls: List[Dict[str, Any]]) -> None:
    """收集下拉框选择，生成锁定文本写回行数据。"""
    tokens: List[str] = []
    for ctrl in lock_controls:
        req = ctrl["req"]
        if ctrl["kind"] == "exti_pair":
            a, b = ctrl["sel_a"].value, ctrl["sel_b"].value
            if a != "auto" and b != "auto":
                pins = sorted([a, b])
                tokens.append(f"{req.role}={pins[0]}")
                tokens.append(f"{req.role}={pins[1]}")
        else:
            val = ctrl["sel"].value
            n = ctrl.get("pin_count", 1)
            if val != "auto":
                pins = ctrl["label_to_pins"][val]
                for p in pins:
                    tokens.append(f"{req.role}={p}")
            else:
                # auto 占位，保证同角色多个实例（如 IN2 × 2）时位置不错位
                for _ in range(n):
                    tokens.append(f"{req.role}=auto")
    # 全部自动 = 未锁定
    if tokens and all(pin.upper() == "AUTO" for _, pin in (parse_lock_text(",".join(tokens)))):
        lock_text = ""
    else:
        lock_text = ",".join(tokens)
    # 本行内引脚重复校验（忽略 auto）
    pin_list = [p for _, p in parse_lock_text(lock_text) if p.upper() != "AUTO"]
    if len(pin_list) != len(set(pin_list)):
        dup = sorted({p for p in pin_list if pin_list.count(p) > 1})
        ui.notify(f"本行锁定引脚重复：{', '.join(dup)}，请调整。", type="negative")
        return
    # 与其他行已锁定引脚冲突校验
    others_locked: set = set()
    for r in rows:
        if r is row_data:
            continue
        for _, pin in parse_lock_text(r.get("lock_text") or ""):
            if pin.upper() != "AUTO":
                others_locked.add(pin)
    conflict = sorted(set(pin_list) & others_locked)
    if conflict:
        ui.notify(f"与其它行已锁定引脚冲突：{', '.join(conflict)}，请重新选择。", type="negative")
        return
    row_data["lock_text"] = lock_text
    row_data["lock_btn"].set_text("🔒 已锁定" if lock_text else "🔓 未锁定")
    row_data["lock_dialog"].close()


def delete_row(row_data: Dict[str, Any]) -> None:
    """删除一行（含该行的锁定对话框）。"""
    global rows
    rows = [r for r in rows if r["row"] is not row_data["row"]]
    try:
        row_data["lock_dialog"].clear()
    except Exception:
        pass
    try:
        row_data["row"].clear()
        row_data["row"].set_visibility(False)
    except Exception:
        pass


def clear_all_rows() -> None:
    """清空所有行。"""
    global rows
    for r in rows:
        try:
            r["lock_dialog"].clear()
        except Exception:
            pass
        try:
            r["row"].clear()
            r["row"].set_visibility(False)
        except Exception:
            pass
    rows = []


def load_default_scenario() -> None:
    """载入示例场景。"""
    clear_all_rows()
    for pid, cnt in DEFAULT_SCENARIO_SPEC:
        add_row(pid, cnt)


def add_reserve_row(pin: str = None) -> None:
    """添加一行保留引脚。"""
    if pin is None:
        pin = "PA0"
    with reserve_rows_container:
        with ui.row().classes("items-center gap-2") as row:
            sel = ui.select(pin_options, value=pin).classes("w-full max-w-xl")
            ui.button("✖", on_click=lambda: delete_reserve_row(row)).props("flat dense color=red")
    reserve_rows.append({"row": row, "select": sel})


def delete_reserve_row(row) -> None:
    """删除一行保留引脚。"""
    global reserve_rows
    reserve_rows = [r for r in reserve_rows if r["row"] is not row]
    try:
        row.clear()
        row.set_visibility(False)
    except Exception:
        pass


def clear_all_reserve_rows() -> None:
    """清空所有保留引脚行。"""
    global reserve_rows
    for r in reserve_rows:
        try:
            r["row"].clear()
            r["row"].set_visibility(False)
        except Exception:
            pass
    reserve_rows = []


def load_default_reserve() -> None:
    """载入默认保留引脚（SWD、BOOT1、晶振）。"""
    clear_all_reserve_rows()
    for pin in sorted(chip.default_reserved):
        add_reserve_row(pin)


def on_solve() -> None:
    spec = build_spec()
    if not spec:
        result_html.set_content('<p style="color:#c0392b">请至少选择一个外设（数量大于 0）。</p>')
        return

    try:
        requests = build_requests_from_spec(spec)
    except ValueError as exc:
        result_html.set_content(
            f'<p style="color:#c0392b;font-weight:bold">❌ 锁定引脚设置错误：</p><p>{html_mod.escape(str(exc))}</p>'
        )
        return

    reserved = [r["select"].value for r in reserve_rows if r["select"].value]

    solver = Solver(chip, reserved=reserved, include_default_reserved=False)
    solutions = solver.solve(requests)

    if not solutions:
        reasons = diagnose_no_solution(chip, requests, reserved)
        items = "".join(f"<li>{r}</li>" for r in reasons)
        result_html.set_content(
            '<p style="color:#c0392b;font-weight:bold">❌ 没有找到可行方案，原因：</p>'
            f"<ul>{items}</ul>"
            "<p>通用建议：减少外设数量、合并共享总线/定时器，或改用软件模拟（软件 I2C / 软件 PWM / GPIO 模拟编码器）。</p>"
        )
        return

    html_parts = [f'<h3 style="margin:0 0 8px">✅ 找到 {len(solutions)} 套方案，显示前 {min(3, len(solutions))} 套：</h3>']
    for i, sol in enumerate(solutions[:3], 1):
        html_parts.append(solution_to_html(chip, sol, i))
    result_html.set_content("".join(html_parts))


# ---------------------------------------------------------------- 页面

with ui.header().classes("items-center justify-between"):
    ui.label("🔌 单片机引脚配置辅助助手").classes("text-xl font-bold")
    ui.label(f"芯片：{chip.name}（{chip.package}）").classes("text-sm")

with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):

    ui.markdown("#### 第一步：添加外设/基础功能")
    with ui.card().classes("w-full p-3"):
        with ui.column().classes("w-full gap-2") as rows_container:
            pass
        with ui.row().classes("gap-2 mt-3"):
            ui.button("➕ 添加外设", on_click=lambda: add_row()).props("flat color=blue")
            ui.button("载入示例场景", on_click=load_default_scenario).props("flat color=green")
            ui.button("清空", on_click=clear_all_rows).props("flat color=grey")
        ui.markdown(
            "每一行：下拉框选外设/功能，数量填几个就加几个（如 GPIO输出 × 3、UART仅RX × 1）。"
            "**PWM 类（步进/电机/舵机/PWM）每一行 = 一个频率组**：组内共用一颗定时器（同频率），不同行独立调速。"
            "**I2C/SPI 每一行 = 一条总线**：数量 = 挂在总线上的设备数。"
            "**I2C 共享组**：选相同组名（A/B/C/D）的多行强制共用一条总线，不同组名强制分开，选「自动」由求解器决定。"
            "**锁定引脚**：点每行的「🔓 未锁定」按钮进入设置，为每个角色选「自动」或具体引脚/候选，"
            "下拉框只列合法选项。"
        ).classes("text-xs text-gray-500 mt-2")

    ui.markdown("#### 第二步：设置保留引脚（分配时绝不使用这些引脚）")
    with ui.card().classes("w-full p-3"):
        with ui.column().classes("w-full gap-2") as reserve_rows_container:
            pass
        with ui.row().classes("gap-2 mt-3"):
            ui.button("➕ 添加保留引脚", on_click=lambda: add_reserve_row()).props("flat color=blue")
            ui.button("恢复默认保留", on_click=load_default_reserve).props("flat color=green")
        ui.markdown(
            "默认已保留 PA13/PA14（SWD 调试口）、PB2（BOOT1）、PD0/PD1（晶振专用）。"
            "如果确定不需要某行，点 ✖ 删除即可。"
        ).classes("text-xs text-gray-500 mt-2")

    ui.button("自动分配", on_click=on_solve).classes("bg-blue-500 text-white text-lg px-6 py-2")

    result_html = ui.html("<p style='color:#888'>点击“自动分配”查看结果。</p>").classes("w-full")

    # 页面打开时默认载入你的痛点场景和默认保留引脚
    load_default_scenario()
    load_default_reserve()


ui.run(host="127.0.0.1", port=8080, reload=False)
