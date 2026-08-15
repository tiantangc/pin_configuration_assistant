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
import asyncio
import csv
import datetime
import html as html_mod
import io
import json
import re
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

# 导出格式选项
EXPORT_FORMATS = {
    "json": "JSON（可再次导入）",
    "md": "Markdown（给人看）",
    "csv": "CSV（Excel/WPS 打开）",
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
result_cards = None
current_solutions: List[Any] = []
current_spec: List[Tuple] = []
export_dialog = None
export_name_input = None
export_format_select = None
pending_export_idx = 0
pinout_dialog = None
pinout_dialog_html = None

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
            remark = (r["remark_input"].value or "").strip()
            spec.append((pid, cnt, bg, lock_text, remark))
    return spec


def add_row(pid: str = None, count: int = 1, bus_group: str = "auto",
            lock_text: str = "", remark: str = "") -> None:
    """在页面上添加一行外设选择。I2C 类外设会额外显示共享组选择。"""
    if pid is None:
        pid = all_periphs[0]["id"]
    with rows_container:
        with ui.row().classes("items-center gap-2") as row:
            num_label = ui.label("").classes("w-6 text-right text-gray-500")
            sel = ui.select(periph_options, value=pid).classes("w-96")
            share_sel = ui.select(BUS_GROUP_OPTIONS, value=bus_group).classes("w-56")
            share_sel.set_visibility(pid in I2C_PERIPH_IDS)
            sel.on_value_change(lambda e: share_sel.set_visibility(e.value in I2C_PERIPH_IDS))
            cnt = ui.number("数量", value=count, min=0, max=16, format="%.0f").classes("w-24")
            remark_input = ui.input("备注", value=remark, placeholder="如 蓝牙").classes("w-36")
            lock_btn = ui.button("🔓 未锁定", on_click=None).props("flat color=blue")
            delete_btn = ui.button("✖").props("flat dense color=red")
        # 锁定设置对话框
        with ui.dialog() as lock_dialog, ui.card().classes("w-96"):
            lock_panel = ui.column().classes("w-full gap-2")
    row_data = {"row": row, "select": sel, "share_sel": share_sel,
                "count": cnt, "remark_input": remark_input,
                "lock_btn": lock_btn, "delete_btn": delete_btn,
                "num_label": num_label,
                "lock_dialog": lock_dialog, "lock_panel": lock_panel,
                "lock_text": lock_text}
    lock_btn.set_text("🔒 已锁定" if lock_text else "🔓 未锁定")
    lock_btn.on_click(lambda: open_lock_dialog(row_data))
    delete_btn.on_click(lambda: delete_row(row_data))
    rows.append(row_data)
    renumber_rows()


def renumber_rows() -> None:
    """重新编号外设行。"""
    for idx, r in enumerate(rows, 1):
        r["num_label"].set_text(f"{idx}.")


def renumber_reserve_rows() -> None:
    """重新编号保留引脚行。"""
    for idx, r in enumerate(reserve_rows, 1):
        r["num_label"].set_text(f"{idx}.")


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


def solution_to_export_rows(spec: List[Tuple], sol) -> List[Dict[str, Any]]:
    """把一套方案转成可导入的行配置：每行外设 + 全引脚锁定文本。"""
    by_row: Dict[str, List[Any]] = {}
    for a in sol.assignments:
        by_row.setdefault(a.req.row_id, []).append(a)

    rows_out: List[Dict[str, Any]] = []
    for idx, item in enumerate(spec):
        pid, cnt = item[0], item[1]
        bg = item[2] if len(item) > 2 else None
        row_id = f"row_{idx}"
        row_reqs = build_requests_from_spec([(pid, cnt, bg, "")])
        assigns = by_row.get(row_id, [])
        used: set = set()
        tokens: List[str] = []
        for rq in row_reqs:
            match = None
            for i, a in enumerate(assigns):
                if i in used:
                    continue
                if (a.req.role == rq.role and a.req.periph_name == rq.periph_name
                        and a.req.count == rq.count and a.req.kind == rq.kind):
                    match = a
                    used.add(i)
                    break
            if match is None:
                continue
            for p in match.pins:
                tokens.append(f"{rq.role}={p}")
        rows_out.append({
            "periph": pid,
            "count": cnt,
            "bus_group": bg,
            "lock_text": ",".join(tokens),
            "remark": item[4] if len(item) > 4 else "",
        })
    return rows_out


def _assignment_remarks(chip: Chip, a, pin: str) -> str:
    remarks = []
    if a.req.locked_pins:
        remarks.append("已锁定")
    if a.shared:
        remarks.append("共享总线")
    if a.remap != 0:
        remarks.append("重映射")
    if pin in chip.board_penalty_pins:
        remarks.append("板级特殊")
    elif pin in chip.penalty_pins:
        remarks.append("特殊脚")
    return "、".join(remarks)


def _resource_summary(chip: Chip, sol) -> List[str]:
    uarts = sorted({a.resource for a in sol.assignments if a.req.kind in ("uart", "uart_tx", "uart_rx")})
    i2cs = sorted({a.resource for a in sol.assignments if a.req.kind == "i2c"})
    spis = sorted({a.resource for a in sol.assignments if a.req.kind in ("spi", "spi_bus")})
    cans = sorted({a.resource for a in sol.assignments if a.req.kind == "can"})
    adcs = sorted({a.resource for a in sol.assignments if a.req.kind == "adc"})
    tims = sorted({a.resource for a in sol.assignments if a.req.kind in ("timer_pwm", "timer_enc", "timer_pwm_exclusive")})
    return [
        f"UART: {', '.join(uarts) if uarts else '无'}",
        f"I2C: {', '.join(i2cs) if i2cs else '无'}",
        f"SPI: {', '.join(spis) if spis else '无'}",
        f"CAN: {', '.join(cans) if cans else '无'}",
        f"ADC: {', '.join(adcs) if adcs else '无'}",
        f"定时器: {', '.join(tims) if tims else '无'}",
    ]


def solution_to_markdown(chip: Chip, sol, idx: int,
                         reserved_remarks: Dict[str, str] = None) -> str:
    """生成人类可读的 Markdown 引脚表。"""
    lines = [
        f"# 引脚配置方案 {idx}（得分 {sol.score}）",
        "",
        "| 外设/角色 | 资源 | 引脚 | 角色 | 备注 |",
        "|---|---|---|---|---|",
    ]
    for a in sol.assignments:
        for disp, pin, role in a.pin_pairs():
            lines.append(
                f"| {disp} | {a.label} | {pin} | {role} | {_assignment_remarks(chip, a, pin)} |")
    lines.append("")
    lines.append("## 资源占用")
    for s in _resource_summary(chip, sol):
        lines.append(f"- {s}")
    lines.append("")
    lines.append("## 保留引脚")
    if reserved_remarks:
        for p, r in sorted(reserved_remarks.items()):
            lines.append(f"- {p}" + (f"（{r}）" if r else ""))
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 说明")
    if sol.notes:
        for n in sol.notes:
            lines.append(f"- {n}")
    else:
        lines.append("- 无特殊说明")
    lines.append("")
    lines.append("## CubeMX 配置步骤")
    lines.append("```text")
    lines.append(solution_to_cubemx_steps(chip, sol, idx))
    lines.append("```")
    return "\n".join(lines)


def solution_to_csv(chip: Chip, sol, idx: int,
                    reserved_remarks: Dict[str, str] = None) -> str:
    """生成 CSV 引脚表（Excel/WPS 打开），末尾带资源占用、保留引脚和说明。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["方案", "得分", "外设/角色", "资源", "引脚", "角色", "备注"])
    for a in sol.assignments:
        for disp, pin, role in a.pin_pairs():
            writer.writerow([idx, sol.score, disp, a.label, pin, role,
                             _assignment_remarks(chip, a, pin)])
    writer.writerow([])
    writer.writerow(["资源占用"])
    for s in _resource_summary(chip, sol):
        writer.writerow([s])
    writer.writerow([])
    writer.writerow(["保留引脚", "备注"])
    if reserved_remarks:
        for p, r in sorted(reserved_remarks.items()):
            writer.writerow([p, r])
    else:
        writer.writerow(["无", ""])
    writer.writerow([])
    writer.writerow(["说明"])
    if sol.notes:
        for n in sol.notes:
            writer.writerow([n])
    else:
        writer.writerow(["无特殊说明"])
    return buf.getvalue()


def _pin_sort_key(pin: str):
    """按端口字母 + 编号排序，方便照着 CubeMX 点。"""
    m = re.match(r"^P([A-Z])(\d+)$", pin)
    if m:
        return (m.group(1), int(m.group(2)))
    return (pin, 0)


def _signal_for_assignment(chip: Chip, a, pin: str, role: str) -> str:
    """根据 assignment 生成 CubeMX 信号名。"""
    res = a.resource or ""
    k = a.req.kind
    if k in ("uart", "uart_tx", "uart_rx"):
        return f"{res}_{role}"
    if k == "i2c":
        return f"{res}_{role}"
    if k == "spi":
        return f"{res}_{role}"
    if k == "spi_bus":
        return f"{res}_{role}"
    if k == "can":
        return f"{res}_{role}"
    if k == "adc":
        return res  # 如 ADC1_IN0
    if k in ("timer_enc", "timer_pwm", "timer_pwm_exclusive"):
        m = re.search(r"CH(\d)", role)
        ch = m.group(1) if m else "?"
        return f"{res}_CH{ch}"
    if k == "exti_gpio":
        exti = chip.pins[pin]["exti"]
        return f"GPIO_EXTI{exti}"
    if k == "gpio":
        return "GPIO_Input" if "输入" in role else "GPIO_Output"
    return role


def solution_to_cubemx_steps(chip: Chip, sol, idx: int) -> str:
    """生成 CubeMX 配置步骤清单（照着点）。"""
    lines: List[str] = []
    lines.append(f"CubeMX 配置步骤（方案 {idx}，得分 {sol.score}）")
    lines.append("")
    lines.append("【基础设置】")
    lines.append(f"1. 新建 STM32CubeMX 工程，芯片选 {chip.name}")
    lines.append("2. System Core → SYS → Debug: Serial Wire（保留 SWD 调试）")
    lines.append("3. RCC → HSE: Crystal/Ceramic Resonator（如果使用外部晶振）")
    lines.append("")

    # 引脚配置（去重 + 排序）
    lines.append("【引脚配置】在 Pinout 视图里逐个左键点击引脚，选择对应信号：")
    seen_pins: set = set()
    pin_rows: List[Tuple[str, str]] = []
    for a in sol.assignments:
        for disp, pin, role in a.pin_pairs():
            sig = _signal_for_assignment(chip, a, pin, role)
            if (pin, sig) in seen_pins:
                continue
            seen_pins.add((pin, sig))
            pin_rows.append((pin, sig))
    pin_rows.sort(key=lambda x: _pin_sort_key(x[0]))
    for pin, sig in pin_rows:
        lines.append(f"  - {pin:<5} → {sig}")
    lines.append("")

    # 外设模式
    lines.append("【外设模式】在左侧 Categories 里逐个配置：")
    uarts = sorted({a.resource for a in sol.assignments if a.req.kind in ("uart", "uart_tx", "uart_rx")})
    for r in uarts:
        lines.append(f"  - {r} → Mode: Asynchronous")
    i2cs = sorted({a.resource for a in sol.assignments if a.req.kind == "i2c"})
    for r in i2cs:
        lines.append(f"  - {r} → Mode: I2C")
    spis = sorted({a.resource for a in sol.assignments if a.req.kind == "spi"})
    for r in spis:
        lines.append(f"  - {r} → Mode: Full-Duplex Master")
    spi_buses = sorted({a.resource for a in sol.assignments if a.req.kind == "spi_bus"})
    for r in spi_buses:
        lines.append(f"  - {r} → Mode: Full-Duplex Master（NSS 用软件 GPIO，不需要硬件 NSS）")
    cans = sorted({a.resource for a in sol.assignments if a.req.kind == "can"})
    for r in cans:
        lines.append(f"  - {r} → Mode: Normal")
    adcs = sorted({a.resource for a in sol.assignments if a.req.kind == "adc"})
    if adcs:
        lines.append(f"  - ADC1 → 使能通道：{', '.join(adcs)}")
    encs = sorted({a.resource for a in sol.assignments if a.req.kind == "timer_enc"})
    for r in encs:
        lines.append(f"  - {r} → Combined Channels: Encoder Mode")
    pwm_timers: Dict[str, set] = {}
    for a in sol.assignments:
        if a.req.kind in ("timer_pwm", "timer_pwm_exclusive"):
            for role in a.roles:
                m = re.search(r"CH(\d)", role)
                if m:
                    pwm_timers.setdefault(a.resource, set()).add(m.group(1))
    for r in sorted(pwm_timers):
        chs = sorted(pwm_timers[r], key=int)
        lines.append(f"  - {r} → 通道 {', '.join(chs)}: PWM Generation")
    exti_lines = sorted({chip.pins[a.pins[0]]["exti"] for a in sol.assignments
                         if a.req.kind == "exti_gpio" and a.pins})
    for exti in exti_lines:
        lines.append(f"  - NVIC → 使能 EXTI line {exti} 中断")
    lines.append("")

    # 重映射
    remaps = []
    for a in sol.assignments:
        if a.remap != 0:
            remaps.append(f"{a.label}（{a.resource}）")
    if remaps:
        lines.append("【重映射提醒】以下外设使用了重映射，CubeMX 中需在对应外设配置里开启 Remap：")
        for r in sorted(set(remaps)):
            lines.append(f"  - ⚠ {r}")
        lines.append("")
    else:
        lines.append("【重映射】本方案全部使用默认引脚映射，无需额外设置。")
        lines.append("")

    lines.append("【最后检查】")
    lines.append("1. 确认没有引脚显示红色冲突")
    lines.append("2. 确认 GPIO 输出脚（DIR/EN/IN1/IN2 等）在 CubeMX 中已设为 GPIO_Output")
    lines.append("3. 点击 GENERATE CODE 生成工程")
    return "\n".join(lines)


# ---------------------------------------------------------------- 引脚图

KIND_COLORS = {
    "uart": "#3b82f6", "uart_tx": "#3b82f6", "uart_rx": "#3b82f6",
    "i2c": "#22c55e",
    "spi": "#a855f7", "spi_bus": "#a855f7",
    "can": "#78350f",
    "timer_enc": "#f97316", "timer_pwm": "#f97316", "timer_pwm_exclusive": "#f97316",
    "adc": "#eab308",
    "exti_gpio": "#ec4899",
    "gpio": "#6b7280",
}

# 你的 C8T6 最小系统板：2×20 排针，调试排针（PA13/PA14）在右边
BOARD_ROW1 = ["G", "G", "3.3V", "RST", "B11", "B10", "B1", "B0", "A7", "A6",
              "A5", "A4", "A3", "A2", "A1", "A0", "C15", "C14", "C13", "VB"]
BOARD_ROW2 = ["B12", "B13", "B14", "B15", "A8", "A9", "A10", "A11", "A12",
              "A15", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "5V", "G", "3.3V"]


def _norm_board_pin(pin: str) -> str:
    """把板子丝印缩写成标准引脚名，方便查颜色。"""
    if pin in ("G", "3.3V", "5V"):
        return pin
    if pin == "VB":
        return "VBAT"
    if pin == "RST":
        return "NRST"
    if re.match(r"^[ABC]\d+$", pin):
        return "P" + pin
    return pin


# 外设名简化，用于引脚图引出标注（保持短小）
SHORT_NAMES = {
    "I2C屏幕": "屏", "MPU6050": "MPU", "OpenMV": "OpenMV", "视觉模块": "视觉",
    "步进电机": "步", "小车电机": "车", "硬件编码器": "编码器",
    "GPIO模拟编码器": "GPIO编码", "GPIO输出": "GPIO出", "GPIO输入": "GPIO入",
    "UART串口": "UART", "UART仅TX": "UART_TX", "UART仅RX": "UART_RX",
    "I2C总线": "I2C", "SPI总线": "SPI", "SPI屏幕": "SPI屏",
    "ADC输入": "ADC", "EXTI中断输入": "EXTI", "CAN总线": "CAN",
    "PWM输出": "PWM", "舵机": "舵机", "DBUS遥控器": "DBUS",
}


def _short_disp(disp: str) -> str:
    """把显示名缩短用于标注：步进电机#1 -> 步#1。"""
    base = disp.split("#")[0]
    short = SHORT_NAMES.get(base, base[:4])
    if "#" in disp:
        short += "#" + disp.split("#", 1)[1]
    return short


def _collect_callouts(sol) -> Dict[str, List[str]]:
    """收集每个引脚的具体用途标注：{pin: [标注文本]}。"""
    callouts: Dict[str, List[str]] = {}
    for a in sol.assignments:
        labels = (a.req.sub_labels
                  if (a.req.sub_labels and len(a.req.sub_labels) == len(a.pins))
                  else [a.req.periph_name] * len(a.pins))
        for i, pin in enumerate(a.pins):
            role = a.roles[i] if i < len(a.roles) else ""
            base = labels[i] if i < len(labels) else a.req.periph_name
            name = a.req.remark if a.req.remark else _short_disp(base)
            label = f"{name} {role}" if role else name
            callouts.setdefault(pin, [])
            if label not in callouts[pin]:
                callouts[pin].append(label)
    return callouts


def _pin_colors(sol) -> Dict[str, str]:
    colors: Dict[str, str] = {}
    for a in sol.assignments:
        color = KIND_COLORS.get(a.req.kind, "#6b7280")
        for p in a.pins:
            colors[p] = color
    return colors


def _pin_color(pin: str, sol_colors: Dict[str, str], reserved: set, chip: Chip) -> str:
    if pin in sol_colors:
        return sol_colors[pin]
    if pin in reserved:
        return "#ef4444"  # 保留 = 红
    info = chip.pins.get(pin)
    if info and info.get("gpio"):
        return "#e5e7eb"  # 空闲可用 = 浅灰
    return "#9ca3af"  # 电源/晶振/调试专用 = 深灰


def _legend_svg(x: float, y: float) -> str:
    items = [
        ("#3b82f6", "UART"), ("#22c55e", "I2C"), ("#a855f7", "SPI"),
        ("#f97316", "定时器"), ("#eab308", "ADC"), ("#ec4899", "EXTI"),
        ("#6b7280", "GPIO"), ("#ef4444", "保留"), ("#e5e7eb", "空闲"),
        ("#9ca3af", "电源/专用"),
    ]
    parts = []
    cx = x
    for color, label in items:
        parts.append(f'<rect x="{cx}" y="{y}" width="10" height="10" fill="{color}" stroke="#374151" stroke-width="0.5"/>')
        parts.append(f'<text x="{cx + 12}" y="{y + 9}" font-size="9" fill="#111827">{label}</text>')
        cx += 52
    return "".join(parts)


def chip_pinout_svg(chip: Chip, sol, reserved: set, reserved_remarks=None) -> str:
    """LQFP48 芯片封装引脚图，带工程图式引出标注。"""
    pin_nums = chip.data.get("package_pin_numbers", {})
    sol_colors = _pin_colors(sol)
    callouts = _collect_callouts(sol)
    parts = ['<svg viewBox="0 0 720 720" xmlns="http://www.w3.org/2000/svg" '
             'style="width:100%;max-width:720px;height:auto">']
    parts.append('<rect width="720" height="720" fill="white"/>')
    parts.append('<rect x="110" y="110" width="420" height="420" rx="10" fill="#f3f4f6" stroke="#9ca3af"/>')
    parts.append('<text x="320" y="300" text-anchor="middle" font-size="14" fill="#374151">STM32F103C8T6</text>')
    parts.append('<text x="320" y="320" text-anchor="middle" font-size="12" fill="#6b7280">LQFP48</text>')
    parts.append('<circle cx="110" cy="110" r="4" fill="#374151"/>')

    top_calls, right_calls, bottom_calls, left_calls = [], [], [], []

    def pin(n: int, x: float, y: float, w: float, h: float,
            tx: float, ty: float, anchor: str, cx: float, cy: float):
        name = pin_nums.get(str(n), "?")
        color = _pin_color(name, sol_colors, reserved, chip)
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{color}" '
                     f'stroke="#374151" stroke-width="0.5"/>')
        parts.append(f'<text x="{tx}" y="{ty}" text-anchor="{anchor}" font-size="8" '
                     f'fill="#111827">{name}</text>')
        label = None
        if name in callouts:
            label = "、".join(callouts[name])
        elif reserved_remarks and name in reserved_remarks and reserved_remarks[name]:
            label = "保留·" + reserved_remarks[name]
        if label:
            if anchor == "middle" and y < 110:      # 顶边
                top_calls.append((cx, label))
            elif anchor == "start":                  # 右边
                right_calls.append((cy, label))
            elif anchor == "middle" and y > 530:    # 底边
                bottom_calls.append((cx, label))
            else:                                    # 左边
                left_calls.append((cy, label))

    for i in range(12):  # 顶边 1-12
        x = 130 + i * 34
        pin(i + 1, x, 78, 16, 32, x + 8, 74, "middle", x + 8, 78)
    for i in range(12):  # 右边 13-24
        y = 130 + i * 34
        pin(i + 13, 540, y, 32, 16, 576, y + 11, "start", 540, y + 8)
    for i in range(12):  # 底边 25-36（右到左）
        x = 526 - i * 34
        pin(i + 25, x, 540, 16, 32, x + 8, 586, "middle", x + 8, 572)
    for i in range(12):  # 左边 37-48（下到上）
        y = 526 - i * 34
        pin(i + 37, 78, y, 32, 16, 74, y + 11, "end", 78, y + 8)

    # 引出标注
    for j, (cx, label) in enumerate(top_calls):
        yt = 48 if j % 2 == 0 else 30
        parts.append(f'<polyline points="{cx},78 {cx},{yt + 7} {cx + 6},{yt + 7}" '
                     f'fill="none" stroke="#374151" stroke-width="0.5"/>')
        parts.append(f'<text x="{cx + 8}" y="{yt + 15}" font-size="8" fill="#111827">{label}</text>')
    for j, (cy, label) in enumerate(right_calls):
        xt = 592 if j % 2 == 0 else 574
        parts.append(f'<polyline points="540,{cy} {xt + 6},{cy} {xt + 6},{cy + 8}" '
                     f'fill="none" stroke="#374151" stroke-width="0.5"/>')
        parts.append(f'<text x="{xt + 8}" y="{cy + 15}" font-size="8" fill="#111827">{label}</text>')
    for j, (cx, label) in enumerate(bottom_calls):
        yt = 600 if j % 2 == 0 else 620
        parts.append(f'<polyline points="{cx},572 {cx},{yt + 7} {cx + 6},{yt + 7}" '
                     f'fill="none" stroke="#374151" stroke-width="0.5"/>')
        parts.append(f'<text x="{cx + 8}" y="{yt + 15}" font-size="8" fill="#111827">{label}</text>')
    for j, (cy, label) in enumerate(left_calls):
        xt = 64 if j % 2 == 0 else 46
        parts.append(f'<polyline points="78,{cy} {xt - 6},{cy} {xt - 6},{cy + 8}" '
                     f'fill="none" stroke="#374151" stroke-width="0.5"/>')
        parts.append(f'<text x="{xt - 8}" y="{cy + 15}" font-size="8" fill="#111827" '
                     f'text-anchor="end">{label}</text>')

    parts.append(_legend_svg(30, 668))
    if reserved_remarks:
        text = "保留: " + ", ".join(f"{p}({r})" if r else p
                                    for p, r in sorted(reserved_remarks.items()))
        parts.append(f'<text x="30" y="692" font-size="8" fill="#374151">{text}</text>')
    parts.append('</svg>')
    return "".join(parts)


def board_pinout_svg(chip: Chip, sol, reserved: set, reserved_remarks=None) -> str:
    """你的 C8T6 最小系统板：2×20 排针（调试排针在右），按丝印横排绘制，带引出标注。"""
    sol_colors = _pin_colors(sol)
    callouts = _collect_callouts(sol)
    parts = ['<svg viewBox="0 0 800 460" xmlns="http://www.w3.org/2000/svg" '
             'style="width:100%;max-width:800px;height:auto">']
    parts.append('<rect width="800" height="460" fill="white"/>')
    parts.append('<rect x="20" y="60" width="760" height="210" rx="8" fill="#d1d5db" stroke="#9ca3af"/>')
    parts.append('<text x="400" y="80" text-anchor="middle" font-size="14" fill="#374151">'
                 'C8T6 最小系统板（调试排针在右）</text>')

    def row(pins, y):
        calls = []
        for i, pin in enumerate(pins):
            x = 35 + i * 37
            std = _norm_board_pin(pin)
            color = _pin_color(std, sol_colors, reserved, chip)
            parts.append(f'<rect x="{x}" y="{y}" width="34" height="40" fill="{color}" '
                         f'stroke="#374151" stroke-width="0.5"/>')
            cx = x + 17
            cy = y + 26
            parts.append(f'<text x="{cx}" y="{cy}" text-anchor="middle" font-size="10" '
                         f'fill="#111827">{pin}</text>')
            label = None
            if std in callouts:
                label = "、".join(callouts[std])
            elif reserved_remarks and std in reserved_remarks and reserved_remarks[std]:
                label = "保留·" + reserved_remarks[std]
            if label:
                calls.append((cx, label))
        return calls

    row1_calls = row(BOARD_ROW1, 125)
    row2_calls = row(BOARD_ROW2, 205)

    # 第一排标注向上引出
    for j, (cx, label) in enumerate(row1_calls):
        yt = 106 if j % 2 == 0 else 88
        parts.append(f'<polyline points="{cx},125 {cx},{yt + 7} {cx + 5},{yt + 7}" '
                     f'fill="none" stroke="#374151" stroke-width="0.5"/>')
        parts.append(f'<text x="{cx + 7}" y="{yt + 15}" font-size="8" fill="#111827">{label}</text>')
    # 第二排标注向下引出
    for j, (cx, label) in enumerate(row2_calls):
        yt = 288 if j % 2 == 0 else 308
        parts.append(f'<polyline points="{cx},245 {cx},{yt + 7} {cx + 5},{yt + 7}" '
                     f'fill="none" stroke="#374151" stroke-width="0.5"/>')
        parts.append(f'<text x="{cx + 7}" y="{yt + 15}" font-size="8" fill="#111827">{label}</text>')

    parts.append('<text x="8" y="150" text-anchor="middle" font-size="10" fill="#374151" '
                 'transform="rotate(-90, 8, 150)">第一排</text>')
    parts.append('<text x="8" y="225" text-anchor="middle" font-size="10" fill="#374151" '
                 'transform="rotate(-90, 8, 225)">第二排</text>')

    parts.append(_legend_svg(30, 405))
    if reserved_remarks:
        text = "保留: " + ", ".join(f"{p}({r})" if r else p
                                    for p, r in sorted(reserved_remarks.items()))
        parts.append(f'<text x="30" y="430" font-size="8" fill="#374151">{text}</text>')
    parts.append('</svg>')
    return "".join(parts)


def show_large_pinout(svg: str) -> None:
    """在弹窗中查看大图（突破卡片内宽度限制）。"""
    large_svg = (svg
                 .replace("max-width:800px", "max-width:1400px")
                 .replace("max-width:640px", "max-width:1400px"))
    pinout_dialog_html.set_content(large_svg)
    pinout_dialog.open()


def ask_export(idx: int) -> None:
    """先让用户确认/修改文件名，再下载。"""
    global pending_export_idx
    if idx < 0 or idx >= len(current_solutions):
        ui.notify("方案不存在，请重新求解后再导出。", type="negative")
        return
    pending_export_idx = idx
    export_name_input.value = f"方案{idx + 1}_引脚表"
    export_dialog.open()


def do_export() -> None:
    """确认文件名和格式后执行下载。"""
    name = (export_name_input.value or "").strip()
    if not name:
        ui.notify("请输入文件名。", type="negative")
        return
    fmt = export_format_select.value or "json"
    ext_map = {"json": ".json", "md": ".md", "csv": ".csv"}
    # 去旧后缀，换新后缀
    base = name
    for ext in ext_map.values():
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    filename = base + ext_map[fmt]
    download_solution(pending_export_idx, filename, fmt)
    export_dialog.close()


def download_solution(idx: int, filename: str, fmt: str) -> None:
    """按格式生成文件并触发浏览器下载。"""
    sol = current_solutions[idx]
    reserved_remarks = {r["select"].value: (r["remark_input"].value or "").strip()
                        for r in reserve_rows if r["select"].value}
    if fmt == "json":
        data = {
            "app": "pin_configuration_assistant",
            "version": 1,
            "chip": chip.id,
            "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "solution_index": idx + 1,
            "score": sol.score,
            "reserved": [{"pin": r["select"].value, "remark": (r["remark_input"].value or "").strip()}
                         for r in reserve_rows if r["select"].value],
            "rows": solution_to_export_rows(current_spec, sol),
        }
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        media = "application/json"
    elif fmt == "md":
        payload = solution_to_markdown(chip, sol, idx + 1, reserved_remarks).encode("utf-8")
        media = "text/markdown"
    elif fmt == "csv":
        payload = solution_to_csv(chip, sol, idx + 1, reserved_remarks).encode("utf-8-sig")
        media = "text/csv"
    else:
        ui.notify("未知导出格式。", type="negative")
        return
    ui.download(payload, filename=filename, media_type=media)
    ui.notify(f"已开始下载：{filename}", type="positive")


async def _read_upload(e) -> bytes:
    """兼容不同 NiceGUI 版本的上传文件内容读取（同步/异步文件对象）。"""
    for attr in ("file", "content"):
        val = getattr(e, attr, None)
        if val is None:
            continue
        if isinstance(val, bytes):
            return val
        if isinstance(val, str):
            return val.encode("utf-8")
        if hasattr(val, "read"):
            data = val.read()
            if asyncio.iscoroutine(data):
                data = await data
            if isinstance(data, bytes):
                return data
            if isinstance(data, str):
                return data.encode("utf-8")
            return bytes(data)
    files = getattr(e, "files", None)
    if isinstance(files, (list, tuple)) and files:
        val = files[0]
        if isinstance(val, bytes):
            return val
        if hasattr(val, "read"):
            data = val.read()
            if asyncio.iscoroutine(data):
                data = await data
            if isinstance(data, bytes):
                return data
            if isinstance(data, str):
                return data.encode("utf-8")
    raise ValueError("无法读取上传文件内容")


async def handle_import(e) -> None:
    """导入方案 JSON：重建页面行，所有引脚全部锁定。"""
    try:
        data = json.loads((await _read_upload(e)).decode("utf-8"))
    except Exception as exc:
        ui.notify(f"文件读取失败：{exc}", type="negative")
        return

    if data.get("chip") != chip.id:
        ui.notify(f"该方案属于芯片 {data.get('chip')}，当前芯片是 {chip.id}，无法导入。", type="negative")
        return

    rows_data = data.get("rows", [])
    if not rows_data:
        ui.notify("文件中没有可导入的外设行。", type="negative")
        return

    clear_all_rows()
    for r in rows_data:
        pid = r.get("periph")
        cnt = int(r.get("count", 1))
        bg = r.get("bus_group") or "auto"
        lock_text = r.get("lock_text") or ""
        remark = r.get("remark") or ""
        add_row(pid, cnt, bg, lock_text, remark)

    # 恢复保留引脚（导出文件里有 reserved 字段就按文件恢复，包括为空的情况）
    reserved = data.get("reserved", None)
    if reserved is not None:
        clear_all_reserve_rows()
        for item in reserved:
            if isinstance(item, dict):
                add_reserve_row(item.get("pin"), item.get("remark") or "")
            else:
                add_reserve_row(item, "")

    ui.notify(
        f"导入成功：{len(rows_data)} 行外设（全部锁定），保留引脚 {len(reserved) if reserved is not None else '按当前页面保留'} 个。"
        "可继续添加外设后点「自动分配」。",
        type="positive")


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
    renumber_rows()


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


def add_reserve_row(pin: str = None, remark: str = "") -> None:
    """添加一行保留引脚。"""
    if pin is None:
        pin = "PA0"
    with reserve_rows_container:
        with ui.row().classes("items-center gap-2") as row:
            num_label = ui.label("").classes("w-6 text-right text-gray-500")
            sel = ui.select(pin_options, value=pin).classes("w-96")
            remark_input = ui.input("备注", value=remark, placeholder="如 板载LED").classes("w-36")
            del_btn = ui.button("✖").props("flat dense color=red")
    row_data = {"row": row, "select": sel, "num_label": num_label,
                "remark_input": remark_input, "del_btn": del_btn}
    del_btn.on_click(lambda: delete_reserve_row(row_data))
    reserve_rows.append(row_data)
    renumber_reserve_rows()


def delete_reserve_row(row_data) -> None:
    """删除一行保留引脚。"""
    global reserve_rows
    reserve_rows = [r for r in reserve_rows if r["row"] is not row_data["row"]]
    try:
        row_data["row"].clear()
        row_data["row"].set_visibility(False)
    except Exception:
        pass
    renumber_reserve_rows()


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
    global current_solutions, current_spec
    spec = build_spec()
    if not spec:
        current_solutions = []
        current_spec = []
        result_html.set_content('<p style="color:#c0392b">请至少选择一个外设（数量大于 0）。</p>')
        result_cards.clear()
        return

    try:
        requests = build_requests_from_spec(spec)
    except ValueError as exc:
        current_solutions = []
        result_html.set_content(
            f'<p style="color:#c0392b;font-weight:bold">❌ 锁定引脚设置错误：</p><p>{html_mod.escape(str(exc))}</p>'
        )
        result_cards.clear()
        return

    reserved = [r["select"].value for r in reserve_rows if r["select"].value]

    solver = Solver(chip, reserved=reserved, include_default_reserved=False)
    solutions = solver.solve(requests)

    if not solutions:
        current_solutions = []
        reasons = diagnose_no_solution(chip, requests, reserved)
        items = "".join(f"<li>{r}</li>" for r in reasons)
        result_html.set_content(
            '<p style="color:#c0392b;font-weight:bold">❌ 没有找到可行方案，原因：</p>'
            f"<ul>{items}</ul>"
            "<p>通用建议：减少外设数量、合并共享总线/定时器，或改用软件模拟（软件 I2C / 软件 PWM / GPIO 模拟编码器）。</p>"
        )
        result_cards.clear()
        return

    current_solutions = solutions
    current_spec = spec
    MAX_SHOWN = 12
    result_html.set_content(
        f'<h3 style="margin:0 0 8px">✅ 找到 {len(solutions)} 套方案，'
        f'显示前 {min(MAX_SHOWN, len(solutions))} 套（点击方框展开查看详情）</h3>'
    )

    # 方案卡片：折叠展示，点开看引脚配置和导出按钮
    result_cards.clear()
    with result_cards:
        reserved_set = set(reserved)
        reserved_remarks = {r["select"].value: (r["remark_input"].value or "").strip()
                            for r in reserve_rows if r["select"].value}
        for i, sol in enumerate(solutions[:MAX_SHOWN], 1):
            with ui.expansion(f"方案 {i} —— 得分 {sol.score}").classes("w-full border rounded-lg"):
                ui.html(solution_to_html(chip, sol, i)).classes("w-full")
                with ui.expansion("📋 CubeMX 配置步骤").classes("w-full border rounded"):
                    steps = solution_to_cubemx_steps(chip, sol, i)
                    ui.html('<pre style="background:#f6f8fa;padding:12px;border-radius:8px;'
                            'overflow-x:auto;font-size:13px;line-height:1.45">'
                            + html_mod.escape(steps) + "</pre>").classes("w-full")
                with ui.expansion("🔲 芯片引脚图（LQFP48）").classes("w-full border rounded"):
                    chip_svg = chip_pinout_svg(chip, sol, reserved_set, reserved_remarks)
                    ui.html(chip_svg).classes("w-full")
                    ui.button("🔍 查看大图", on_click=lambda svg=chip_svg: show_large_pinout(svg)).props("flat color=blue")
                with ui.expansion("🔌 最小系统板引脚图").classes("w-full border rounded"):
                    board_svg = board_pinout_svg(chip, sol, reserved_set, reserved_remarks)
                    ui.html(board_svg).classes("w-full")
                    ui.button("🔍 查看大图", on_click=lambda svg=board_svg: show_large_pinout(svg)).props("flat color=blue")
                ui.button(f"⬇ 导出方案{i}",
                          on_click=lambda i=i: ask_export(i - 1)).props("flat color=green")


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
            ui.upload(on_upload=handle_import, auto_upload=True,
                      label="📥 导入方案 JSON").props("flat color=orange")
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
    with ui.column().classes("w-full gap-2") as result_cards:
        pass

    # 导出文件名确认对话框
    with ui.dialog() as export_dialog, ui.card().classes("w-96"):
        ui.label("确认导出的文件名和格式").classes("font-bold")
        export_name_input = ui.input("文件名", value="").classes("w-full")
        export_format_select = ui.select(EXPORT_FORMATS, value="json").classes("w-full")
        with ui.row().classes("gap-2 mt-2"):
            ui.button("确认下载", on_click=do_export).props("color=blue")
            ui.button("取消", on_click=lambda: export_dialog.close()).props("flat")

    # 引脚图大图对话框
    with ui.dialog() as pinout_dialog, ui.card().classes("w-full max-w-7xl"):
        pinout_dialog_html = ui.html("").classes("w-full")

    # 页面打开时默认载入你的痛点场景和默认保留引脚
    load_default_scenario()
    load_default_reserve()


ui.run(host="127.0.0.1", port=8080, reload=False)
