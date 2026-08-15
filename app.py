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
                    diagnose_no_solution, format_solution,
                    list_peripherals, load_chip)

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
    """按页面上每一行收集 (外设id, 数量, I2C共享组)。每一行 = 一个频率组/总线。"""
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
            spec.append((pid, cnt, bg))
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
            ui.button("✖", on_click=lambda: delete_row(row)).props("flat dense color=red")
    rows.append({"row": row, "select": sel, "share_sel": share_sel, "count": cnt})


def delete_row(row) -> None:
    """删除一行。"""
    global rows
    rows = [r for r in rows if r["row"] is not row]
    try:
        row.clear()
        row.set_visibility(False)
    except Exception:
        pass


def clear_all_rows() -> None:
    """清空所有行。"""
    global rows
    for r in rows:
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

    requests = build_requests_from_spec(spec)

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
            "例：4 个步进前两个同速后两个同速 → 两行「步进电机 × 2」；"
            "I2C屏和 MPU6050 想共享 → 两行都选「共享组 A」。"
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
