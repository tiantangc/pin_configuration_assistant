#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
引脚配置辅助工具 —— 命令行入口（M0）
用法示例：
    python cli.py                          # 运行内置示例场景
    python cli.py --list                   # 列出可用芯片和外设模板
    python cli.py --scenario "i2c_screen:1,mpu6050:1,openmv:1,stepper_motor:3,car_motor:2,encoder_hw:1,encoder_gpio:1"
    python cli.py --scenario "stepper_motor:4,openmv:1" --top 5 --reserve PA0,PA1
"""
import argparse
import sys

from solver import (load_chip, list_peripherals, build_requests_from_spec,
                    diagnose_no_solution, Solver, format_solution,
                    CHIP_DIR, PERIPH_DIR)

DEFAULT_SCENARIO = ("i2c_screen:1,mpu6050:1,openmv:1,stepper_motor:3,"
                    "car_motor:2,encoder_hw:1,encoder_gpio:1")
DEFAULT_SPEC = [
    ("i2c_screen", 1),
    ("mpu6050", 1),
    ("openmv", 1),
    ("stepper_motor", 3),
    ("car_motor", 2),
    ("encoder_hw", 1),
    ("encoder_gpio", 1),
]


def parse_spec(text: str):
    """解析命令行 --scenario：外设id:数量，逗号分隔。"""
    spec = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            pid, num = part.split(":", 1)
            spec.append((pid.strip(), int(num.strip())))
        else:
            spec.append((part, 1))
    return spec


def cmd_list() -> None:
    print("== 可用芯片 ==")
    import os
    for fn in sorted(os.listdir(CHIP_DIR)):
        if fn.endswith(".json"):
            print("  " + fn[:-5])
    print("\n== 可用外设模板 ==")
    for p in list_peripherals():
        print(f"  {p['id']:<16} {p['name']:<10} {p.get('description', '')}")
    print("\n--scenario 用法：外设id:数量，多个用逗号分隔，如 openmv:1,stepper_motor:3")


def main() -> None:
    parser = argparse.ArgumentParser(description="STM32 引脚配置辅助工具（M0）")
    parser.add_argument("--chip", default="STM32F103C8Tx", help="芯片 id（chips 目录下的 json 文件名）")
    parser.add_argument("--scenario", default=None,
                        help="外设清单，如 i2c_screen:1,mpu6050:1,openmv:1,stepper_motor:3")
    parser.add_argument("--top", type=int, default=3, help="输出方案数（默认 3）")
    parser.add_argument("--reserve", default="", help="额外保留不分配的引脚，逗号分隔，如 PA0,PA1")
    parser.add_argument("--list", action="store_true", help="列出可用芯片和外设")
    args = parser.parse_args()

    if args.list:
        cmd_list()
        return

    chip = load_chip(args.chip)
    if args.scenario:
        spec = parse_spec(args.scenario)
    else:
        print(f"未指定 --scenario，使用内置示例场景：{DEFAULT_SCENARIO}\n")
        spec = list(DEFAULT_SPEC)

    if not spec:
        print("场景为空，请用 --scenario 指定外设。")
        sys.exit(1)

    print(f"芯片: {chip.name} ({chip.package})")
    print("外设清单（每一行 = 一个 PWM 频率组）:")
    for pid, num in spec:
        print(f"  - {pid} × {num}")
    print()

    requests = build_requests_from_spec(spec)
    reserved = [p.strip().upper() for p in args.reserve.split(",") if p.strip()]
    if reserved:
        print(f"额外保留引脚: {', '.join(reserved)}\n")

    solver = Solver(chip, reserved=reserved)
    solutions = solver.solve(requests)

    if not solutions:
        print("❌ 没有找到可行方案，原因：")
        diag_reserved = list(chip.default_reserved) + reserved
        for reason in diagnose_no_solution(chip, requests, diag_reserved):
            print(f"  - {reason}")
        print("通用建议：减少外设数量、合并共享总线/定时器，或改用软件模拟（软件 I2C / 软件 PWM / GPIO 模拟编码器）。")
        sys.exit(2)

    print(f"找到 {len(solutions)} 套方案，以下为前 {min(args.top, len(solutions))} 套：\n")
    for i, sol in enumerate(solutions[:args.top], 1):
        print(format_solution(chip, sol, i))
        print()


if __name__ == "__main__":
    main()
