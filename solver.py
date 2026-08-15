#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
STM32 引脚配置辅助工具 —— 求解引擎（M0 里程碑）
纯标准库实现，无需安装任何第三方包。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from itertools import combinations, product
from typing import Any, Dict, List, Optional, Tuple

# Windows 控制台中文兼容
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHIP_DIR = os.path.join(BASE_DIR, "chips")
PERIPH_DIR = os.path.join(BASE_DIR, "peripherals")

# 资源分配优先级：越稀缺越先分配
KIND_ORDER = {
    "uart": 0,
    "uart_tx": 0,
    "uart_rx": 0,
    "can": 0,
    "i2c": 1,
    "spi": 2,
    "spi_bus": 2,
    "timer_enc": 3,
    "timer_pwm_exclusive": 3,
    "timer_pwm": 4,
    "adc": 5,
    "exti_gpio": 6,
    "gpio": 7,
}

TIMER_CHANNEL_COUNT = 4


# ---------------------------------------------------------------- 数据结构

@dataclass
class Request:
    """一个待分配的引脚/资源请求。"""
    kind: str                 # uart / i2c / spi / timer_enc / timer_pwm / exti_gpio / gpio
    periph_name: str          # 显示用外设名，如 "步进电机#1"
    role: str                 # 角色，如 "STEP" / "DIR" / "TX-RX"
    count: int = 1            # 需要引脚数（timer_pwm 为通道数，exti_gpio 为引脚数）
    share_group: Optional[str] = None
    bus_group: Optional[str] = None   # I2C 共享组：相同组名强制共用一条总线
    row_id: Optional[str] = None      # 页面行标识（用于锁定引脚按行匹配）
    locked_pins: Optional[List[str]] = None  # 锁定引脚：与该请求候选 pins 等长
    remark: Optional[str] = None      # 行备注（如串口备注成"蓝牙"）
    optional: bool = False
    sub_labels: Optional[List[str]] = None   # 合并请求中，每个通道/引脚对应的实例名

    def display_name(self) -> str:
        if self.sub_labels and len(self.sub_labels) == self.count:
            if len(set(self.sub_labels)) == 1:
                return f"{self.sub_labels[0]} {self.role}"
            return f"{len(self.sub_labels)}个 {self.role}"
        return f"{self.periph_name} {self.role}"


@dataclass
class Assignment:
    """一次成功分配。"""
    req: Request
    resource: Optional[str]       # 外设实例名，如 USART1 / TIM2 / I2C1
    remap: int
    label: str
    pins: List[str]               # 实际引脚
    roles: List[str]              # 与 pins 对齐的角色
    shared: bool = False          # 是否共享了总线（如多个 I2C 从机共用）

    def pin_pairs(self) -> List[Tuple[str, str, str]]:
        """返回 [(显示名, 引脚, 角色), ...]，显示名带行备注。"""
        labels = self.req.sub_labels if (self.req.sub_labels and len(self.req.sub_labels) == len(self.pins)) \
            else [f"{self.req.periph_name} {r}" for r in self.roles]
        if self.req.remark:
            labels = [f"{l}[{self.req.remark}]" for l in labels]
        return list(zip(labels, self.pins, self.roles))


@dataclass
class Solution:
    assignments: List[Assignment]
    score: int
    notes: List[str]


# ---------------------------------------------------------------- 芯片类

class Chip:
    """封装一颗芯片的引脚/复用数据库。"""

    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self.id = data["id"]
        self.name = data.get("name", data["id"])
        self.package = data.get("package", "")
        self.family = data.get("family", "")
        self.pins: Dict[str, Dict[str, Any]] = data["pins"]
        self.groups: Dict[str, Any] = data.get("peripheral_groups", {})
        self.default_reserved: set = set(data.get("reserved_pins_by_default", []))
        self.penalty_pins: Dict[str, str] = data.get("penalty_pins", {})
        self.board_penalty_pins: Dict[str, str] = data.get("board_penalty_pins", {})
        self._cand_cache: Dict[str, Any] = {}

    # ----- 基础候选 -----

    def gpio_candidates(self, reserved: set) -> List[str]:
        out = []
        for pin, info in self.pins.items():
            if info.get("gpio") and pin not in reserved:
                out.append(pin)
        # 优先不用特殊脚，再按名称排序保证稳定
        out.sort(key=lambda p: (p in self.penalty_pins, p in self._adc_pins(), p))
        return out

    def exti_candidates(self, reserved: set) -> List[str]:
        return [p for p in self.gpio_candidates(reserved) if self.pins[p].get("exti") is not None]

    def _adc_pins(self) -> set:
        return {p for p, i in self.pins.items() if i.get("adc")}

    # ----- 外设候选生成（返回列表，每个元素为一个候选 dict） -----

    def uart_candidates(self) -> List[Dict[str, Any]]:
        if "uart" in self._cand_cache:
            return self._cand_cache["uart"]
        out = []
        for uart_id, groups in self.groups.get("UART", {}).items():
            if isinstance(groups, list):
                # F103 绑定组格式
                for g in groups:
                    out.append({
                        "resource": uart_id,
                        "remap": g["remap"],
                        "label": g["label"],
                        "pins": [g["tx"], g["rx"]],
                        "roles": ["TX", "RX"],
                        "tx": g["tx"], "rx": g["rx"],
                    })
            else:
                # MSPM0 灵活复用格式：TX/RX 各自独立选择
                for tx in groups.get("tx", []):
                    for rx in groups.get("rx", []):
                        if tx == rx:
                            continue
                        out.append({
                            "resource": uart_id,
                            "remap": 0,
                            "label": groups.get("label", uart_id),
                            "pins": [tx, rx],
                            "roles": ["TX", "RX"],
                            "tx": tx, "rx": rx,
                        })
        return out

    def i2c_candidates(self) -> List[Dict[str, Any]]:
        if "i2c" in self._cand_cache:
            return self._cand_cache["i2c"]
        out = []
        for i2c_id, groups in self.groups.get("I2C", {}).items():
            if isinstance(groups, list):
                # F103 绑定组格式
                for g in groups:
                    out.append({
                        "resource": i2c_id,
                        "remap": g["remap"],
                        "label": g["label"],
                        "pins": [g["scl"], g["sda"]],
                        "roles": ["SCL", "SDA"],
                    })
            else:
                for scl in groups.get("scl", []):
                    for sda in groups.get("sda", []):
                        if scl == sda:
                            continue
                        out.append({
                            "resource": i2c_id,
                            "remap": 0,
                            "label": groups.get("label", i2c_id),
                            "pins": [scl, sda],
                            "roles": ["SCL", "SDA"],
                        })
        return out

    def spi_candidates(self) -> List[Dict[str, Any]]:
        if "spi" in self._cand_cache:
            return self._cand_cache["spi"]
        out = []
        for spi_id, groups in self.groups.get("SPI", {}).items():
            if isinstance(groups, list):
                # F103 绑定组格式
                for g in groups:
                    out.append({
                        "resource": spi_id,
                        "remap": g["remap"],
                        "label": g["label"],
                        "pins": [g["nss"], g["sck"], g["miso"], g["mosi"]],
                        "roles": ["NSS", "SCK", "MISO", "MOSI"],
                    })
            else:
                # MSPM0：完整 4 线 SPI 组合，CS 限制前 4 个避免爆炸
                picos = groups.get("pico", [])[:4]
                pocis = groups.get("poci", [])[:4]
                scks = groups.get("sck", [])[:4]
                css = groups.get("cs", [])[:4]
                for pico in picos:
                    for poci in pocis:
                        for sck in scks:
                            for cs in css:
                                pins = [cs, sck, poci, pico]
                                if len(set(pins)) != 4:
                                    continue
                                out.append({
                                    "resource": spi_id,
                                    "remap": 0,
                                    "label": groups.get("label", spi_id),
                                    "pins": pins,
                                    "roles": ["NSS", "SCK", "MISO", "MOSI"],
                                })
        return out

    def spi_bus_candidates(self) -> List[Dict[str, Any]]:
        """SPI 总线（软件 NSS 模式）：只共享 SCK/MISO/MOSI 三线。"""
        out = []
        for spi_id, groups in self.groups.get("SPI", {}).items():
            if isinstance(groups, list):
                for g in groups:
                    out.append({
                        "resource": spi_id,
                        "remap": g["remap"],
                        "label": g["label"] + "（软件NSS）",
                        "pins": [g["sck"], g["miso"], g["mosi"]],
                        "roles": ["SCK", "MISO", "MOSI"],
                    })
            else:
                for pico in groups.get("pico", [])[:4]:
                    for poci in groups.get("poci", [])[:4]:
                        for sck in groups.get("sck", [])[:4]:
                            pins = [sck, poci, pico]
                            if len(set(pins)) != 3:
                                continue
                            out.append({
                                "resource": spi_id,
                                "remap": 0,
                                "label": groups.get("label", spi_id) + "（软件NSS）",
                                "pins": pins,
                                "roles": ["SCK", "MISO", "MOSI"],
                            })
        return out

    def can_candidates(self) -> List[Dict[str, Any]]:
        out = []
        for can_id, groups in self.groups.get("CAN", {}).items():
            if isinstance(groups, list):
                for g in groups:
                    out.append({
                        "resource": can_id,
                        "remap": g["remap"],
                        "label": g["label"],
                        "pins": [g["rx"], g["tx"]],
                        "roles": ["RX", "TX"],
                    })
            else:
                for tx in groups.get("tx", []):
                    for rx in groups.get("rx", []):
                        if tx == rx:
                            continue
                        out.append({
                            "resource": can_id,
                            "remap": 0,
                            "label": groups.get("label", can_id),
                            "pins": [rx, tx],
                            "roles": ["RX", "TX"],
                        })
        return out

    def adc_candidates(self) -> List[Dict[str, Any]]:
        """兼容 F103 的 ADC1_INn 与 MSPM0 的 A0_x / A1_x 两种模拟通道格式。"""
        out = []
        for pin, info in self.pins.items():
            adc_list = info.get("adc") or []
            for sig in adc_list:
                if sig.startswith("ADC1_IN"):
                    try:
                        channel = int(sig.split("_IN")[1])
                    except ValueError:
                        continue
                    out.append({
                        "resource": sig,
                        "channel": channel,
                        "remap": 0,
                        "label": sig,
                        "pins": [pin],
                        "roles": ["ADC"],
                    })
                elif sig.startswith("A0_") or sig.startswith("A1_"):
                    out.append({
                        "resource": sig,
                        "channel": sig,
                        "remap": 0,
                        "label": sig,
                        "pins": [pin],
                        "roles": ["ADC"],
                    })
        out.sort(key=lambda c: (str(c["channel"]), c["pins"][0]))
        return out

    def timer_enc_candidates(self) -> List[Dict[str, Any]]:
        if "timer_enc" in self._cand_cache:
            return self._cand_cache["timer_enc"]
        out = []
        for timer_id, groups in self.groups.get("TIMER", {}).items():
            if isinstance(groups, list):
                # F103 绑定组格式
                for g in groups:
                    ch = g.get("channels", {})
                    if "1" in ch and "2" in ch:
                        out.append({
                            "resource": timer_id,
                            "remap": g["remap"],
                            "label": g["label"],
                            "pins": [ch["1"], ch["2"]],
                            "roles": ["编码器A(CH1)", "编码器B(CH2)"],
                            "channels": ["1", "2"],
                        })
            else:
                # MSPM0：取 C0/C1 两个通道自由组合
                ch = groups.get("channels", {})
                c0 = ch.get("C0", [])[:6]
                c1 = ch.get("C1", [])[:6]
                for p0 in c0:
                    for p1 in c1:
                        if p0 == p1:
                            continue
                        out.append({
                            "resource": timer_id,
                            "remap": 0,
                            "label": groups.get("label", timer_id),
                            "pins": [p0, p1],
                            "roles": ["编码器A(C0)", "编码器B(C1)"],
                            "channels": ["C0", "C1"],
                        })
        self._cand_cache["timer_enc"] = out
        return out

    def timer_pwm_candidates(self, count: int) -> List[Dict[str, Any]]:
        """选一个定时器，取 count 个通道；兼容 F103 绑定组与 MSPM0 灵活格式。"""
        key = f"timer_pwm_{count}"
        if key in self._cand_cache:
            return self._cand_cache[key]
        out = []
        for timer_id, groups in self.groups.get("TIMER", {}).items():
            if isinstance(groups, list):
                # F103 绑定组格式
                for g in groups:
                    ch = g.get("channels", {})
                    ch_names = sorted(ch.keys(), key=lambda x: int(x))
                    if len(ch_names) < count:
                        continue
                    for combo in combinations(ch_names, count):
                        pins = [ch[n] for n in combo]
                        out.append({
                            "resource": timer_id,
                            "remap": g["remap"],
                            "label": g["label"],
                            "pins": pins,
                            "roles": [f"CH{n}" for n in combo],
                            "channels": list(combo),
                        })
            else:
                # MSPM0 灵活格式：通道名 C0/C1/... 各自可映射到多个引脚
                ch = groups.get("channels", {})
                ch_names = sorted(ch.keys(), key=lambda x: int(x[1:]) if x[1:].isdigit() else 99)
                if len(ch_names) < count:
                    continue
                for combo in combinations(ch_names, count):
                    pin_lists = [ch[n][:4] for n in combo]
                    for pins in product(*pin_lists):
                        if len(set(pins)) != len(pins):
                            continue
                        out.append({
                            "resource": timer_id,
                            "remap": 0,
                            "label": groups.get("label", timer_id),
                            "pins": list(pins),
                            "roles": list(combo),
                            "channels": list(combo),
                        })
        self._cand_cache[key] = out
        return out

    def board_positions(self) -> Dict[str, Tuple[int, int]]:
        """构建引脚名 -> (列, 行) 的板子位置表。"""
        if "board_pos" in self._cand_cache:
            return self._cand_cache["board_pos"]
        pos: Dict[str, Tuple[int, int]] = {}
        layout = self.data.get("board_layout") or {}
        cols = layout.get("columns") or []
        norm = layout.get("norm") or {}
        for ci, col in enumerate(cols):
            for ri, raw in enumerate(col):
                pin = norm.get(raw, raw)
                pos[pin] = (ci, ri)
        self._cand_cache["board_pos"] = pos
        return pos

    def pins_close(self, pin_a: str, pin_b: str) -> bool:
        """判断两个引脚在板子上是否物理靠近。

        - 两排布局（F103 最小系统板）：同一排且相邻。
        - 四列布局（MSPM0 天猛星）：同列相邻；或相邻近列（列1-2、列3-4）且行差≤1。
        """
        pos = self.board_positions()
        if pin_a not in pos or pin_b not in pos:
            return False
        ca, ra = pos[pin_a]
        cb, rb = pos[pin_b]
        ncols = len((self.data.get("board_layout") or {}).get("columns") or [])
        if ncols >= 4:
            if ca == cb:
                return abs(ra - rb) == 1
            near_cols = (abs(ca - cb) == 1) and ({ca, cb} in ({0, 1}, {2, 3}))
            return near_cols and abs(ra - rb) <= 1
        # 两排布局
        return ca == cb and abs(ra - rb) == 1


# ---------------------------------------------------------------- 求解器状态

class State:
    """回溯搜索中的资源占用状态。"""

    def __init__(self):
        self.used_pins: set = set()
        self.uart_used: Dict[str, int] = {}
        self.spi_used: Dict[str, int] = {}
        self.can_used: Dict[str, int] = {}
        self.i2c_used: set = set()          # {(i2c_id, remap)}
        self.i2c_remap: Dict[str, int] = {} # i2c_id -> remap（同一外设 remap 全局唯一）
        self.i2c_group_owner: Dict[str, tuple] = {}  # 共享组名 -> (i2c_id, remap)
        self.i2c_group_used: Dict[tuple, str] = {}   # (i2c_id, remap) -> 占用它的共享组名
        self.timer_remap: Dict[str, int] = {} # timer_id -> remap
        self.timer_enc_used: set = set()    # 被编码器整个占用的定时器
        self.timer_excl_used: set = set()   # 被独占 PWM（如独立转速步进）整个占用的定时器
        self.timer_pwm_used: Dict[str, set] = {}  # timer_id -> 已占通道号
        self.exti_used: set = set()         # 已占用的 EXTI 线号
        self.adc_used: set = set()          # 已占用的 ADC 通道号

    def snapshot(self) -> Dict[str, Any]:
        return {
            "used_pins": set(self.used_pins),
            "uart_used": dict(self.uart_used),
            "spi_used": dict(self.spi_used),
            "can_used": dict(self.can_used),
            "i2c_used": set(self.i2c_used),
            "i2c_remap": dict(self.i2c_remap),
            "i2c_group_owner": dict(self.i2c_group_owner),
            "i2c_group_used": dict(self.i2c_group_used),
            "timer_remap": dict(self.timer_remap),
            "timer_enc_used": set(self.timer_enc_used),
            "timer_excl_used": set(self.timer_excl_used),
            "timer_pwm_used": {k: set(v) for k, v in self.timer_pwm_used.items()},
            "exti_used": set(self.exti_used),
            "adc_used": set(self.adc_used),
        }

    def restore(self, snap: Dict[str, Any]) -> None:
        self.used_pins = snap["used_pins"]
        self.uart_used = snap["uart_used"]
        self.spi_used = snap["spi_used"]
        self.can_used = snap["can_used"]
        self.i2c_used = snap["i2c_used"]
        self.i2c_remap = snap["i2c_remap"]
        self.i2c_group_owner = snap["i2c_group_owner"]
        self.i2c_group_used = snap["i2c_group_used"]
        self.timer_remap = snap["timer_remap"]
        self.timer_enc_used = snap["timer_enc_used"]
        self.timer_excl_used = snap["timer_excl_used"]
        self.timer_pwm_used = snap["timer_pwm_used"]
        self.exti_used = snap["exti_used"]
        self.adc_used = snap["adc_used"]


# ---------------------------------------------------------------- 求解器

class Solver:
    def __init__(self, chip: Chip, reserved: Optional[List[str]] = None,
                 max_solutions: int = 60, max_steps: int = 30000,
                 include_default_reserved: bool = True):
        self.chip = chip
        if include_default_reserved:
            self.reserved: set = set(chip.default_reserved)
        else:
            self.reserved: set = set()
        if reserved:
            self.reserved.update(p.upper() for p in reserved)
        self.max_solutions = max_solutions
        self.max_steps = max_steps
        self.solutions: List[List[Assignment]] = []
        self._steps = 0

    # ----- 主入口 -----

    def solve(self, requests: List[Request]) -> List[Solution]:
        self.solutions = []
        self._steps = 0
        ordered = sorted(requests, key=lambda r: (KIND_ORDER.get(r.kind, 9), -r.count))
        self._backtrack(ordered, 0, State(), [])
        scored: List[Solution] = []
        for sol in self.solutions:
            score, notes = self._score(sol)
            scored.append(Solution(list(sol), score, notes))
        scored.sort(key=lambda s: -s.score)
        # 去重（按引脚分配签名）
        uniq: List[Solution] = []
        seen: set = set()
        for s in scored:
            sig = tuple(sorted((a.resource or a.req.kind, a.remap, tuple(a.pins)) for a in s.assignments))
            if sig not in seen:
                seen.add(sig)
                uniq.append(s)
        return uniq

    # ----- 回溯 -----

    def _backtrack(self, reqs: List[Request], idx: int, state: State, sol: List[Assignment]) -> None:
        if len(self.solutions) >= self.max_solutions:
            return
        if self._steps >= self.max_steps:
            return
        self._steps += 1

        if idx == len(reqs):
            self.solutions.append(list(sol))
            return

        req = reqs[idx]
        for cand in self._candidates(req, state):
            snap = state.snapshot()
            if self._try_apply(req, cand, state):
                sol.append(Assignment(req=req,
                                      resource=cand.get("resource"),
                                      remap=cand.get("remap", 0),
                                      label=cand.get("label", ""),
                                      pins=list(cand.get("pins", [])),
                                      roles=list(cand.get("roles", [])),
                                      shared=bool(cand.get("shared"))))
                self._backtrack(reqs, idx + 1, state, sol)
                sol.pop()
            state.restore(snap)

    # ----- 候选生成（带剪枝） -----

    def _candidates(self, req: Request, state: State) -> List[Dict[str, Any]]:
        if req.kind == "uart":
            cands = []
            for c in self.chip.uart_candidates():
                rid, remap = c["resource"], c["remap"]
                if rid in state.uart_used and state.uart_used[rid] != remap:
                    continue
                if not self._pin_conflict(state, c["pins"]):
                    cands.append(c)
        elif req.kind == "uart_tx":
            cands = []
            for c in self.chip.uart_candidates():
                rid, remap = c["resource"], c["remap"]
                if rid in state.uart_used and state.uart_used[rid] != remap:
                    continue
                pin = c["tx"]
                if pin not in state.used_pins:
                    cands.append({
                        "resource": rid, "remap": remap, "label": c["label"],
                        "pins": [pin], "roles": ["TX"],
                    })
        elif req.kind == "uart_rx":
            cands = []
            for c in self.chip.uart_candidates():
                rid, remap = c["resource"], c["remap"]
                if rid in state.uart_used and state.uart_used[rid] != remap:
                    continue
                pin = c["rx"]
                if pin not in state.used_pins:
                    cands.append({
                        "resource": rid, "remap": remap, "label": c["label"],
                        "pins": [pin], "roles": ["RX"],
                    })
        elif req.kind == "can":
            cands = [c for c in self.chip.can_candidates()
                     if (c["resource"] not in state.can_used
                         or state.can_used[c["resource"]] == c["remap"])
                     and not self._pin_conflict(state, c["pins"])]
        elif req.kind == "i2c":
            cands = []
            for c in self.chip.i2c_candidates():
                rid, remap = c["resource"], c["remap"]
                if rid in state.i2c_remap and state.i2c_remap[rid] != remap:
                    continue
                key = (rid, remap)
                bg = req.bus_group
                if bg:
                    # 指定了共享组：同组强制共用一条总线
                    if bg in state.i2c_group_owner:
                        if state.i2c_group_owner[bg] != key:
                            continue
                        c = dict(c)
                        c["shared"] = True
                        cands.append(c)
                    else:
                        # 组尚未绑定：不能占用已被其他组或自动请求占用的总线
                        if key in state.i2c_group_used:
                            continue
                        if self._pin_conflict(state, c["pins"]):
                            continue
                        c = dict(c)
                        c["shared"] = False
                        cands.append(c)
                else:
                    # 自动模式：优先共享，但不去抢已指定给某个组的总线
                    if key in state.i2c_group_used:
                        continue
                    if key in state.i2c_used:
                        c = dict(c)
                        c["shared"] = True
                        cands.append(c)
                    elif not self._pin_conflict(state, c["pins"]):
                        c = dict(c)
                        c["shared"] = False
                        cands.append(c)
        elif req.kind == "spi":
            cands = [c for c in self.chip.spi_candidates()
                     if c["resource"] not in state.spi_used
                     and not self._pin_conflict(state, c["pins"])]
        elif req.kind == "spi_bus":
            cands = [c for c in self.chip.spi_bus_candidates()
                     if c["resource"] not in state.spi_used
                     and not self._pin_conflict(state, c["pins"])]
        elif req.kind == "timer_enc":
            cands = []
            for c in self.chip.timer_enc_candidates():
                rid, remap = c["resource"], c["remap"]
                if rid in state.timer_enc_used or rid in state.timer_excl_used:
                    continue
                if rid in state.timer_pwm_used:
                    continue
                if rid in state.timer_remap and state.timer_remap[rid] != remap:
                    continue
                if self._pin_conflict(state, c["pins"]):
                    continue
                cands.append(c)
        elif req.kind == "timer_pwm_exclusive":
            cands = []
            for c in self.chip.timer_pwm_candidates(req.count):
                rid, remap = c["resource"], c["remap"]
                if rid in state.timer_enc_used or rid in state.timer_excl_used:
                    continue
                if rid in state.timer_pwm_used:
                    continue
                if rid in state.timer_remap and state.timer_remap[rid] != remap:
                    continue
                if self._pin_conflict(state, c["pins"]):
                    continue
                cands.append(c)
        elif req.kind == "timer_pwm":
            cands = []
            for c in self.chip.timer_pwm_candidates(req.count):
                rid, remap = c["resource"], c["remap"]
                if rid in state.timer_enc_used or rid in state.timer_excl_used:
                    continue
                if rid in state.timer_remap and state.timer_remap[rid] != remap:
                    continue
                if rid in state.timer_pwm_used:
                    if any(ch in state.timer_pwm_used[rid] for ch in c["channels"]):
                        continue
                if self._pin_conflict(state, c["pins"]):
                    continue
                cands.append(c)
        elif req.kind == "adc":
            cands = []
            for c in self.chip.adc_candidates():
                if c["channel"] in state.adc_used:
                    continue
                if c["pins"][0] in state.used_pins:
                    continue
                cands.append(c)
        elif req.kind == "exti_gpio":
            avail = []
            for p in self.chip.exti_candidates(self.reserved):
                if p in state.used_pins:
                    continue
                exti = self.chip.pins[p]["exti"]
                if exti in state.exti_used:
                    continue
                avail.append(p)
            cands = []
            for combo in combinations(avail, req.count):
                exts = [self.chip.pins[p]["exti"] for p in combo]
                if len(set(exts)) != len(exts):
                    continue
                roles = ["A相", "B相"] if req.count == 2 else [req.role] * req.count
                cands.append({
                    "resource": None,
                    "remap": 0,
                    "label": "GPIO + 外部中断",
                    "pins": list(combo),
                    "roles": roles,
                })
        elif req.kind == "gpio":
            cands = [{
                "resource": None,
                "remap": 0,
                "label": "GPIO",
                "pins": [p],
                "roles": [req.role],
            } for p in self.chip.gpio_candidates(self.reserved) if p not in state.used_pins]
        else:
            cands = []
        # 统一过滤：保留引脚对所有类型（含 UART/定时器复用）一律不可用
        cands = [c for c in cands
                 if not any(p in self.reserved for p in c.get("pins", []))]
        # 锁定引脚：只保留与锁定引脚完全匹配的候选
        if req.locked_pins:
            cands = [c for c in cands
                     if len(c.get("pins", [])) == len(req.locked_pins)
                     and all(p == lp for p, lp in zip(c["pins"], req.locked_pins))]
        # 排序：remap 小的、不碰特殊脚的优先
        cands.sort(key=lambda c: (c.get("remap", 0), self._cand_penalty(c)))
        return cands

    def _cand_penalty(self, cand: Dict[str, Any]):
        """候选排序惩罚：优先避开板级特殊脚，其次普通特殊脚。"""
        board = sum(1 for p in cand.get("pins", []) if p in self.chip.board_penalty_pins)
        normal = sum(1 for p in cand.get("pins", []) if p in self.chip.penalty_pins)
        return (board, normal)

    # ----- 硬约束应用 -----

    def _pin_conflict(self, state: State, pins: List[str]) -> bool:
        if len(set(pins)) != len(pins):
            return True
        return any(p in state.used_pins for p in pins)

    def _try_apply(self, req: Request, cand: Dict[str, Any], state: State) -> bool:
        kind = req.kind
        rid = cand.get("resource")
        remap = cand.get("remap", 0)
        pins = cand.get("pins", [])
        channels = cand.get("channels", [])

        # 双保险：保留引脚一律不可用（无论 GPIO 还是硬件复用）
        if any(p in self.reserved for p in pins):
            return False

        if kind == "uart":
            if rid in state.uart_used and state.uart_used[rid] != remap:
                return False
            if self._pin_conflict(state, pins):
                return False
            state.uart_used[rid] = remap
            state.used_pins.update(pins)
            return True

        if kind == "uart_tx":
            if rid in state.uart_used and state.uart_used[rid] != remap:
                return False
            if self._pin_conflict(state, pins):
                return False
            state.uart_used[rid] = remap
            state.used_pins.update(pins)
            return True

        if kind == "uart_rx":
            if rid in state.uart_used and state.uart_used[rid] != remap:
                return False
            if self._pin_conflict(state, pins):
                return False
            state.uart_used[rid] = remap
            state.used_pins.update(pins)
            return True

        if kind == "can":
            if rid in state.can_used and state.can_used[rid] != remap:
                return False
            if self._pin_conflict(state, pins):
                return False
            state.can_used[rid] = remap
            state.used_pins.update(pins)
            return True

        if kind == "spi":
            if rid in state.spi_used:
                return False
            if self._pin_conflict(state, pins):
                return False
            state.spi_used[rid] = remap
            state.used_pins.update(pins)
            return True

        if kind == "spi_bus":
            if rid in state.spi_used:
                return False
            if self._pin_conflict(state, pins):
                return False
            state.spi_used[rid] = remap
            state.used_pins.update(pins)
            return True

        if kind == "i2c":
            key = (rid, remap)
            if rid in state.i2c_remap and state.i2c_remap[rid] != remap:
                return False
            bg = req.bus_group
            if bg:
                if bg in state.i2c_group_owner:
                    # 同组共享：必须是同一 (i2c_id, remap)
                    return state.i2c_group_owner[bg] == key
                # 新组绑定：不能占用已被别的组/自动请求占用的总线
                if key in state.i2c_group_used:
                    return False
                if self._pin_conflict(state, pins):
                    return False
                state.i2c_group_owner[bg] = key
                state.i2c_group_used[key] = bg
                state.i2c_used.add(key)
                state.i2c_remap[rid] = remap
                state.used_pins.update(pins)
                return True
            # 自动模式：不去抢已指定给某个组的总线
            if key in state.i2c_group_used:
                return False
            if key in state.i2c_used:
                # 共享同一条 I2C 总线：引脚已被同一组占用，直接允许
                return True
            if self._pin_conflict(state, pins):
                return False
            state.i2c_used.add(key)
            state.i2c_remap[rid] = remap
            state.used_pins.update(pins)
            return True

        if kind == "timer_enc":
            if rid in state.timer_enc_used or rid in state.timer_excl_used:
                return False
            if rid in state.timer_pwm_used:
                return False
            if rid in state.timer_remap and state.timer_remap[rid] != remap:
                return False
            if self._pin_conflict(state, pins):
                return False
            state.timer_enc_used.add(rid)
            state.timer_remap[rid] = remap
            state.used_pins.update(pins)
            return True

        if kind == "timer_pwm_exclusive":
            if rid in state.timer_enc_used or rid in state.timer_excl_used:
                return False
            if rid in state.timer_pwm_used:
                return False
            if rid in state.timer_remap and state.timer_remap[rid] != remap:
                return False
            if self._pin_conflict(state, pins):
                return False
            state.timer_remap[rid] = remap
            state.timer_excl_used.add(rid)
            state.used_pins.update(pins)
            return True

        if kind == "timer_pwm":
            if rid in state.timer_enc_used or rid in state.timer_excl_used:
                return False
            if rid in state.timer_remap and state.timer_remap[rid] != remap:
                return False
            if rid in state.timer_pwm_used:
                if any(ch in state.timer_pwm_used[rid] for ch in channels):
                    return False
            if self._pin_conflict(state, pins):
                return False
            state.timer_remap[rid] = remap
            state.timer_pwm_used.setdefault(rid, set()).update(channels)
            state.used_pins.update(pins)
            return True

        if kind == "exti_gpio":
            for p in pins:
                if p in state.used_pins:
                    return False
                exti = self.chip.pins[p]["exti"]
                if exti in state.exti_used:
                    return False
            state.used_pins.update(pins)
            state.exti_used.update(self.chip.pins[p]["exti"] for p in pins)
            return True

        if kind == "adc":
            channel = cand.get("channel")
            if channel is None:
                return False
            if channel in state.adc_used:
                return False
            if self._pin_conflict(state, pins):
                return False
            state.adc_used.add(channel)
            state.used_pins.update(pins)
            return True

        if kind == "gpio":
            if self._pin_conflict(state, pins):
                return False
            state.used_pins.update(pins)
            return True

        return False

    # ----- 评分 -----

    def _score(self, sol: List[Assignment]) -> Tuple[int, List[str]]:
        score = 0
        notes: List[str] = []
        i2c_shared_count = 0
        for a in sol:
            if a.req.kind == "i2c" and a.shared:
                score += 10
                i2c_shared_count += 1
            if a.remap == 0:
                score += 2
            else:
                score += 0
            if a.req.kind in ("timer_pwm", "timer_pwm_exclusive") and a.req.count > 1:
                score += 5 * a.req.count  # 多路 PWM 同定时器成组
            if a.req.kind == "timer_enc":
                score += 3
            # 成对/成组外设：引脚在板子上物理靠近加分（如 UART TX/RX、I2C SCL/SDA）
            if a.req.kind in ("uart", "i2c", "spi", "spi_bus", "can", "timer_enc", "exti_gpio") and len(a.pins) >= 2:
                close_pairs = 0
                for i in range(len(a.pins) - 1):
                    if self.chip.pins_close(a.pins[i], a.pins[i + 1]):
                        close_pairs += 1
                if close_pairs:
                    score += 6 * close_pairs
                    notes.append(f"{a.req.periph_name} {a.req.role} 引脚相邻 ×{close_pairs}，布线方便")
            for p in a.pins:
                if p in self.chip.board_penalty_pins:
                    score -= 6
                    notes.append(f"{p} 为板级特殊脚（{self.chip.board_penalty_pins[p]}）")
                elif p in self.chip.penalty_pins:
                    score -= 3
                    notes.append(f"{p} 为特殊脚（{self.chip.penalty_pins[p]}）")
        # 保留高级定时器 TIM1 加分
        used_timers = {a.resource for a in sol
                       if a.req.kind in ("timer_pwm", "timer_enc", "timer_pwm_exclusive")}
        if "TIM1" in self.chip.groups.get("TIMER", {}) and "TIM1" not in used_timers:
            score += 3
            notes.append("已保留 TIM1 高级定时器")
        if i2c_shared_count:
            notes.append(f"{i2c_shared_count + 1} 个 I2C 外设共享同一条总线，省引脚")
        return score, notes


# ---------------------------------------------------------------- 场景构建

def load_chip(chip_id: str) -> Chip:
    path = os.path.join(CHIP_DIR, f"{chip_id}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到芯片文件：{path}")
    with open(path, "r", encoding="utf-8") as f:
        return Chip(json.load(f))


def load_peripheral(periph_id: str) -> Dict[str, Any]:
    path = os.path.join(PERIPH_DIR, f"{periph_id}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到外设模板：{path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_peripherals() -> List[Dict[str, Any]]:
    out = []
    if os.path.isdir(PERIPH_DIR):
        for fn in sorted(os.listdir(PERIPH_DIR)):
            if fn.endswith(".json"):
                with open(os.path.join(PERIPH_DIR, fn), "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not data.get("hidden", False):
                        out.append(data)
    return out


def build_requests(instances: List[Tuple[Dict[str, Any], str]]) -> List[Request]:
    """instances: [(外设模板 dict, 显示名), ...]"""
    plain: List[Request] = []
    pwm_groups: Dict[str, List[Request]] = {}

    for template, disp in instances:
        for r in template.get("requests", []):
            kind = r["type"]
            role = r.get("role", "")
            count = int(r.get("count", 1))
            share_group = r.get("share_group")
            optional = bool(r.get("optional", False))
            req = Request(kind=kind, periph_name=disp, role=role, count=count,
                          share_group=share_group, optional=optional)
            if kind == "timer_pwm" and share_group:
                pwm_groups.setdefault(share_group, []).append(req)
            else:
                plain.append(req)

    # 合并同 share_group 的 PWM 请求（如 3 个步进电机 STEP 合成一个 3 通道请求）
    for group, reqs in pwm_groups.items():
        total = sum(r.count for r in reqs)
        labels: List[str] = []
        for r in reqs:
            labels.extend([r.periph_name] * r.count)
        # 一个定时器最多 4 通道，超过拆分
        for start in range(0, total, TIMER_CHANNEL_COUNT):
            chunk = labels[start:start + TIMER_CHANNEL_COUNT]
            if not chunk:
                continue
            merged = Request(kind="timer_pwm",
                             periph_name=reqs[0].periph_name,
                             role=reqs[0].role,
                             count=len(chunk),
                             share_group=group,
                             optional=False,
                             sub_labels=list(chunk))
            plain.append(merged)

    return plain


def candidate_options_for_request(chip: Chip, req: "Request", reserved: set,
                                  blocked: Optional[set] = None,
                                  max_options: int = 150) -> List[Tuple[str, List[str]]]:
    """返回某请求的合法锁定候选：[(显示名, 引脚列表), ...]。

    用于锁定设置界面的下拉框——选项全部来自芯片真实复用表，保证引脚合法。
    blocked: 已锁定的引脚集合（其他行锁定），这些引脚不再出现在选项中。
    """
    reserved = {p.upper() for p in reserved}
    blocked = {p.upper() for p in (blocked or set())}

    def ok(pins: List[str]) -> bool:
        return not any(p in reserved or p in blocked for p in pins)

    opts: List[Tuple[str, List[str]]] = []
    k = req.kind

    if k == "uart":
        for c in chip.uart_candidates():
            if ok(c["pins"]):
                opts.append((f"{c['label']}（{', '.join(c['pins'])}）", list(c["pins"])))
    elif k == "uart_tx":
        for c in chip.uart_candidates():
            pins = [c["tx"]]
            if ok(pins):
                opts.append((f"{c['label']} TX（{c['tx']}）", pins))
    elif k == "uart_rx":
        for c in chip.uart_candidates():
            pins = [c["rx"]]
            if ok(pins):
                opts.append((f"{c['label']} RX（{c['rx']}）", pins))
    elif k == "i2c":
        for c in chip.i2c_candidates():
            if ok(c["pins"]):
                opts.append((f"{c['label']}（{', '.join(c['pins'])}）", list(c["pins"])))
    elif k == "spi":
        for c in chip.spi_candidates():
            if ok(c["pins"]):
                opts.append((f"{c['label']}（{', '.join(c['pins'])}）", list(c["pins"])))
    elif k == "spi_bus":
        for c in chip.spi_bus_candidates():
            if ok(c["pins"]):
                opts.append((f"{c['label']}（{', '.join(c['pins'])}）", list(c["pins"])))
    elif k == "can":
        for c in chip.can_candidates():
            if ok(c["pins"]):
                opts.append((f"{c['label']}（{', '.join(c['pins'])}）", list(c["pins"])))
    elif k == "adc":
        for c in chip.adc_candidates():
            if ok(c["pins"]):
                opts.append((f"{c['label']}（{c['pins'][0]}）", list(c["pins"])))
    elif k == "timer_enc":
        for c in chip.timer_enc_candidates():
            if ok(c["pins"]):
                opts.append((f"{c['label']}（{', '.join(c['pins'])}）", list(c["pins"])))
    elif k == "timer_pwm_exclusive":
        for c in chip.timer_pwm_candidates(req.count):
            if ok(c["pins"]):
                opts.append((f"{c['label']} {'/'.join(c['roles'])}（{', '.join(c['pins'])}）",
                             list(c["pins"])))
    elif k == "gpio":
        for p in chip.gpio_candidates(reserved):
            if p not in blocked:
                opts.append((p, [p]))
    elif k == "exti_gpio" and req.count == 1:
        for p in chip.exti_candidates(reserved):
            if p not in blocked:
                opts.append((p, [p]))
    elif k == "exti_gpio":
        # count>=2 时组合太多，由界面拆成 A/B 两个独立下拉
        pass
    return opts[:max_options]


def parse_lock_text(text: str) -> List[Tuple[Optional[str], str]]:
    """解析锁定输入。

    支持两种写法：
    - 带角色：TX=PB10,RX=PB11   （同角色多引脚可重复写：STEP=PA0,STEP=PA1,DIR=PB0）
    - 纯引脚：PB10,PB11          （按该行请求顺序分配）
    返回 [(角色或None, 引脚), ...]
    """
    tokens: List[Tuple[Optional[str], str]] = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            role, pin = part.split("=", 1)
            role = role.strip().upper()
            pin = pin.strip().upper()
            if not role or not pin:
                raise ValueError(f"锁定格式错误：{part!r}，正确格式如 TX=PB10")
            tokens.append((role, pin))
        else:
            pin = part.strip().upper()
            if not pin:
                continue
            tokens.append((None, pin))
    return tokens


def _request_pin_count(req: Request) -> int:
    """请求实际占用的引脚数（count 对 PWM/EXTI 是通道数，对总线是设备数）。"""
    k = req.kind
    if k in ("timer_pwm", "timer_pwm_exclusive", "exti_gpio"):
        return req.count
    return _PIN_COUNT_BY_KIND.get(k, 1)


def _apply_locks_to_row(row_reqs: List[Request], tokens: List[Tuple[Optional[str], str]]) -> None:
    """把锁定 tokens 应用到同一行（row_id 相同）的请求上。"""
    if not tokens:
        return
    role_tokens = [(r, p) for r, p in tokens if r]
    plain_tokens = [p for r, p in tokens if not r]

    if role_tokens:
        # 按角色分组
        by_role: Dict[str, List[str]] = {}
        for r, p in role_tokens:
            by_role.setdefault(r, []).append(p)
        # 校验角色是否存在
        known_roles = {req.role.upper() for req in row_reqs}
        for r in by_role:
            if r not in known_roles:
                raise ValueError(
                    f"锁定角色 {r} 在该外设中不存在，可选角色：{', '.join(sorted(known_roles))}")
        # 按行内请求顺序，把引脚依次分配给同角色的多个请求
        # token 中 pin 为 auto 表示该位置不锁定（用于区分同角色第几个实例）
        role_cursor = {r: 0 for r in by_role}
        for req in row_reqs:
            role = req.role.upper()
            pins = by_role.get(role)
            if not pins:
                continue
            n = _request_pin_count(req)
            start = role_cursor[role]
            chunk = pins[start:start + n]
            if len(chunk) != n:
                raise ValueError(
                    f"锁定角色 {role} 需要 {n} 个引脚，但只提供了 {len(pins) - start} 个：{pins[start:]}")
            non_auto = [p for p in chunk if p.upper() != "AUTO"]
            if len(non_auto) == n:
                req.locked_pins = non_auto
            elif len(non_auto) == 0:
                req.locked_pins = None
            else:
                raise ValueError(
                    f"锁定角色 {role} 的同一请求必须全部锁定或全部自动：{chunk}")
            role_cursor[role] = start + n
        for role in by_role:
            if role_cursor[role] != len(by_role[role]):
                raise ValueError(
                    f"锁定角色 {role} 给了 {len(by_role[role])} 个引脚，但该行只用了 {role_cursor[role]} 个。")
    elif plain_tokens:
        # 纯引脚列表：按行内请求顺序分配
        total = sum(_request_pin_count(r) for r in row_reqs)
        if len(plain_tokens) != total:
            raise ValueError(
                f"锁定引脚数量 {len(plain_tokens)} 与该行所需引脚数 {total} 不一致。"
                f"该行角色顺序：{'、'.join(r.role + '×' + str(_request_pin_count(r)) for r in row_reqs)}")
        idx = 0
        for req in row_reqs:
            n = _request_pin_count(req)
            req.locked_pins = plain_tokens[idx:idx + n]
            idx += n


def build_requests_from_spec(spec: List[Tuple[str, int]]) -> List[Request]:
    """按页面行构建请求。

    spec 元素支持 2~4 元组：
      (外设id, 数量)
      (外设id, 数量, I2C共享组)
      (外设id, 数量, I2C共享组, 锁定文本)

    每个 spec 条目 = 页面上一行 = 一个频率组/总线。
    - 一行内的 PWM 类请求合并为一组，独占一颗定时器，组内同频。
    - I2C：bus_group 相同的行强制共用一条总线，不同组强制分开，None/auto 为自动。
    - 锁定文本：见 parse_lock_text。
    """
    plain: List[Request] = []

    for row_idx, item in enumerate(spec):
        pid, num = item[0], item[1]
        bus_group = item[2] if len(item) > 2 else None
        if bus_group == "auto":
            bus_group = None
        lock_text = item[3] if len(item) > 3 else None
        remark = item[4] if len(item) > 4 else None
        if num <= 0:
            continue
        template = load_peripheral(pid)
        base_name = template.get("name", pid)
        row_group = f"row_{row_idx}"
        unit_names = [base_name] if num <= 1 else [f"{base_name}#{i}" for i in range(1, num + 1)]
        lock_tokens = parse_lock_text(lock_text)

        row_reqs: List[Request] = []
        for r in template.get("requests", []):
            kind = r["type"]
            role = r.get("role", "")
            count = int(r.get("count", 1))
            optional = bool(r.get("optional", False))

            if kind in ("timer_pwm", "timer_pwm_exclusive"):
                # 行内 PWM 合并为一组，独占一颗定时器（一行 = 一个频率组）
                row_reqs.append(Request(
                    kind="timer_pwm_exclusive", periph_name=base_name, role=role,
                    count=num * count, share_group=row_group, row_id=row_group,
                    remark=remark,
                    optional=False, sub_labels=list(unit_names) * count))
            elif kind in ("i2c", "spi_bus"):
                # 总线型：一行 = 一条总线，数量 = 挂在这条总线上的设备数
                disp_name = base_name if num <= 1 else f"{base_name} ×{num}"
                row_reqs.append(Request(
                    kind=kind, periph_name=disp_name, role=role, count=1,
                    share_group=row_group, row_id=row_group,
                    remark=remark,
                    bus_group=bus_group if kind == "i2c" else None, optional=False))
            else:
                # 其他（GPIO/UART/ADC/EXTI/CAN 等）按每个实例逐个生成
                for unit_name in unit_names:
                    row_reqs.append(Request(
                        kind=kind, periph_name=unit_name, role=role, count=count,
                        share_group=r.get("share_group"), row_id=row_group,
                        remark=remark, optional=optional))

        # 应用锁定（在 PWM 拆分前，锁定只支持一行 ≤4 个 PWM 通道）
        _apply_locks_to_row(row_reqs, lock_tokens)

        for req in row_reqs:
            if req.kind == "timer_pwm_exclusive" and req.count > 4:
                if req.locked_pins:
                    raise ValueError("一行超过 4 个 PWM 通道时暂不支持锁定引脚，请拆成多行。")
                # 拆分成多颗定时器（每颗最多 4 通道）
                labels = req.sub_labels or [req.periph_name] * req.count
                for start in range(0, req.count, TIMER_CHANNEL_COUNT):
                    chunk = labels[start:start + TIMER_CHANNEL_COUNT]
                    if not chunk:
                        continue
                    plain.append(Request(kind="timer_pwm_exclusive",
                                         periph_name=req.periph_name, role=req.role,
                                         count=len(chunk), share_group=req.share_group,
                                         row_id=req.row_id, remark=req.remark,
                                         sub_labels=list(chunk)))
            else:
                plain.append(req)
    return plain


def build_default_scenario() -> List[Tuple[Dict[str, Any], str]]:
    """用户痛点场景：I2C 屏 + OpenMV + MPU6050 + 3 步进 + 2 小车电机 + 1 硬编码器 + 1 GPIO 编码器。"""
    spec = [
        ("i2c_screen", 1),
        ("mpu6050", 1),
        ("openmv", 1),
        ("stepper_motor", 3),
        ("car_motor", 2),
        ("encoder_hw", 1),
        ("encoder_gpio", 1),
    ]
    return expand_scenario(spec)


def expand_scenario(spec: List[Tuple[str, int]]) -> List[Tuple[Dict[str, Any], str]]:
    """把 [(外设id, 数量), ...] 展开为 [(模板, 显示名), ...]"""
    instances: List[Tuple[Dict[str, Any], str]] = []
    for pid, num in spec:
        template = load_peripheral(pid)
        name = template.get("name", pid)
        if num <= 1:
            instances.append((template, name))
        else:
            for i in range(1, num + 1):
                instances.append((template, f"{name}#{i}"))
    return instances


def parse_scenario(text: str) -> List[Tuple[Dict[str, Any], str]]:
    spec: List[Tuple[str, int]] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            pid, num = part.split(":", 1)
            spec.append((pid.strip(), int(num.strip())))
        else:
            spec.append((part, 1))
    return expand_scenario(spec)


# ---------------------------------------------------------------- 无方案诊断

# 每种请求类型大致占用的引脚数（启发式）
_PIN_COUNT_BY_KIND = {
    "uart": 2, "uart_tx": 1, "uart_rx": 1, "can": 2,
    "i2c": 2, "spi": 4, "spi_bus": 3,
    "timer_enc": 2, "adc": 1, "exti_gpio": 1, "gpio": 1,
}


def diagnose_no_solution(chip: Chip, requests: List[Request],
                         reserved: Optional[List[str]] = None) -> List[str]:
    """给出无方案时的具体原因（资源不足/可能冲突）。"""
    reserved_set = {p.upper() for p in (reserved or [])}
    reasons: List[str] = []

    timer_need = 0
    uart_full = uart_tx = uart_rx = 0
    i2c_groups: set = set()
    i2c_auto = 0
    spi_need = can_need = adc_need = exti_pins = gpio_need = 0
    pin_need = 0
    has_locks = False

    for r in requests:
        if r.locked_pins:
            has_locks = True
        k = r.kind
        if k in ("timer_enc", "timer_pwm_exclusive"):
            timer_need += 1
            pin_need += r.count if k == "timer_pwm_exclusive" else 2
        elif k == "timer_pwm":
            timer_need += (r.count + 3) // 4
            pin_need += r.count
        elif k == "uart":
            uart_full += 1
            pin_need += 2
        elif k == "uart_tx":
            uart_tx += 1
            pin_need += 1
        elif k == "uart_rx":
            uart_rx += 1
            pin_need += 1
        elif k == "i2c":
            if r.bus_group:
                i2c_groups.add(r.bus_group)
            else:
                i2c_auto += 1
            pin_need += 2
        elif k in ("spi", "spi_bus"):
            spi_need += 1
            pin_need += _PIN_COUNT_BY_KIND[k]
        elif k == "can":
            can_need += 1
            pin_need += 2
        elif k == "adc":
            adc_need += 1
            pin_need += 1
        elif k == "exti_gpio":
            exti_pins += r.count
            pin_need += r.count
        elif k == "gpio":
            gpio_need += 1
            pin_need += 1

    avail_timers = len(chip.groups.get("TIMER", {}))
    avail_uarts = len(chip.groups.get("UART", {}))
    avail_i2cs = len(chip.groups.get("I2C", {}))
    avail_spis = len(chip.groups.get("SPI", {}))
    avail_cans = len(chip.groups.get("CAN", {}))
    avail_adcs = len(chip.adc_candidates())
    avail_gpios = len(chip.gpio_candidates(reserved_set))

    if timer_need > avail_timers:
        reasons.append(
            f"定时器不足：需要约 {timer_need} 颗，芯片只有 {avail_timers} 颗（TIM1~TIM4）。"
            "建议：把需要同速的 PWM 放到同一行、编码器改用 GPIO 模拟，或换 F407。")

    uart_need_min = uart_full + max(uart_tx, uart_rx)
    if uart_need_min > avail_uarts:
        reasons.append(
            f"UART 不足：至少需要 {uart_need_min} 路，芯片只有 {avail_uarts} 路 USART。")

    i2c_need_max = len(i2c_groups) + i2c_auto
    if len(i2c_groups) > avail_i2cs:
        reasons.append(
            f"I2C 总线不足：明确指定了 {len(i2c_groups)} 条独立总线，芯片只有 {avail_i2cs} 条。")
    elif i2c_need_max > avail_i2cs:
        reasons.append(
            f"I2C 总线可能不足：自动模式下最多需要 {i2c_need_max} 条，芯片只有 {avail_i2cs} 条。"
            "建议：给部分 I2C 行设置相同的共享组（如都选 A）。")

    if spi_need > avail_spis:
        reasons.append(
            f"SPI 不足：需要 {spi_need} 路，芯片只有 {avail_spis} 路。")

    if can_need > avail_cans:
        reasons.append(
            f"CAN 不足：需要 {can_need} 路，芯片只有 {avail_cans} 路。")

    if adc_need > avail_adcs:
        reasons.append(
            f"ADC 通道不足：需要 {adc_need} 路，芯片只有 {avail_adcs} 路外部通道（IN0~IN9）。")

    if exti_pins > 16:
        reasons.append(
            f"EXTI 中断线不足：需要 {exti_pins} 条，芯片只有 16 条。")

    if gpio_need > avail_gpios:
        reasons.append(
            f"GPIO 引脚不足：需要 {gpio_need} 个，可用约 {avail_gpios} 个。")

    if pin_need > avail_gpios:
        reasons.append(
            f"引脚总数不足：各类外设共约需 {pin_need} 个引脚，芯片可用约 {avail_gpios} 个。"
            "建议：共享总线、减少外设数量或取消部分保留引脚。")

    if not reasons:
        reasons.append(
            "资源数量上勉强够，但引脚复用/重映射组合冲突导致无解。"
            "建议：放宽保留引脚、调整共享组，或减少同时使用的硬件外设。")

    # 软件模拟建议：列出当前用到的硬件外设中可软件替代的项
    soft = []
    kinds = {r.kind for r in requests}
    if "i2c" in kinds:
        soft.append("I2C → 软件 I2C（任意 2 个 GPIO，建议 ≤100kHz）")
    if "spi" in kinds or "spi_bus" in kinds:
        soft.append("SPI → 软件 SPI（GPIO 翻转 SCK/MOSI/MISO，适合低速器件）")
    if "timer_enc" in kinds:
        soft.append("硬件编码器 → GPIO 模拟编码器（外部中断计数，牺牲高频性能）")
    if "timer_pwm" in kinds or "timer_pwm_exclusive" in kinds:
        soft.append("PWM → 软件 PWM（定时器中断+GPIO 翻转，适合舵机/低速步进）")
    if "uart" in kinds or "uart_tx" in kinds or "uart_rx" in kinds:
        soft.append("UART → 软件串口（仅建议低速 ≤9600 场景）")
    if soft:
        reasons.append("软件模拟建议：" + "；".join(soft) + "。")

    if has_locks:
        reasons.append(
            "检测到手动锁定引脚：请检查锁定引脚是否被保留、是否与其他锁定/自动分配冲突。")

    return reasons


# ---------------------------------------------------------------- 结果格式化

def format_solution(chip: Chip, sol: Solution, idx: int) -> str:
    lines: List[str] = []
    lines.append(f"方案 {idx}  （得分 {sol.score}）")
    lines.append("-" * 78)
    lines.append(f"{'外设/角色':<22}{'资源':<20}{'引脚分配':<26}{'备注':<10}")
    lines.append("-" * 78)
    for a in sol.assignments:
        for disp, pin, role in a.pin_pairs():
            resource = a.label if a.resource is None else a.label
            remark = []
            if a.req.locked_pins:
                remark.append("已锁定")
            if a.shared:
                remark.append("共享总线")
            if a.remap != 0:
                remark.append("重映射")
            if pin in chip.board_penalty_pins:
                remark.append("板级特殊")
            elif pin in chip.penalty_pins:
                remark.append("特殊脚")
            pin_str = f"{pin}({role})"
            lines.append(f"{disp:<22}{resource:<20}{pin_str:<26}{','.join(remark):<10}")
    lines.append("-" * 78)
    # 资源占用
    uarts = sorted({a.resource for a in sol.assignments if a.req.kind in ("uart", "uart_tx", "uart_rx")})
    i2cs = sorted({a.resource for a in sol.assignments if a.req.kind == "i2c"})
    spis = sorted({a.resource for a in sol.assignments if a.req.kind in ("spi", "spi_bus")})
    cans = sorted({a.resource for a in sol.assignments if a.req.kind == "can"})
    adcs = sorted({a.resource for a in sol.assignments if a.req.kind == "adc"})
    tims = sorted({a.resource for a in sol.assignments
                   if a.req.kind in ("timer_pwm", "timer_enc", "timer_pwm_exclusive")})
    lines.append(f"UART 占用: {', '.join(uarts) if uarts else '无'}")
    lines.append(f"I2C  占用: {', '.join(i2cs) if i2cs else '无'}")
    lines.append(f"SPI  占用: {', '.join(spis) if spis else '无'}")
    lines.append(f"CAN  占用: {', '.join(cans) if cans else '无'}")
    lines.append(f"ADC  占用: {', '.join(adcs) if adcs else '无'}")
    lines.append(f"定时器占用: {', '.join(tims) if tims else '无'}")
    if sol.notes:
        lines.append("说明: " + "；".join(sol.notes))
    return "\n".join(lines)
