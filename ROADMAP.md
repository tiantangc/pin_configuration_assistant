# 单片机引脚配置辅助助手 —— 项目路线图与备忘

> 更新日期：2026-08-15
> 用途：防止忘记项目进展、下一步计划、维护方法。

---

## 一、项目状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 命令行求解器 + F103C8T6 引脚库 + 7 个外设模板 | ✅ 完成并验证 |
| M1 | 网页版（NiceGUI）：动态增减外设行、保留引脚行、I2C 共享组、无方案原因诊断 | ✅ 完成，待最终验证 |
| Git | 建仓库推送到 GitHub | ✅ 已推送 https://github.com/tiantangc/pin_configuration_assistant |
| M2 | 锁定引脚重算 / CubeMX 步骤生成 / 导出引脚表 / 引脚图可视化 | 🔲 下一步 |
| M3 | MSPM0G3507 天猛星开发板支持 | 🔲 待做 |
| M4 | 场景模板 / 方案保存 / 软件模拟建议 | 🔲 待做 |
| M5 | F407 支持（最后做） | 🔲 待做 |

---

## 二、已实现功能

### 外设模板（peripherals/ 目录，共 21 个）

**成品外设**：I2C屏幕、MPU6050、OpenMV、步进电机、小车电机、硬件编码器、GPIO模拟编码器、DBUS遥控器、舵机、SPI屏幕

**基础功能**：GPIO输入、GPIO输出、PWM输出、UART串口、UART仅TX、UART仅RX、I2C总线、SPI总线、ADC输入、EXTI中断输入、CAN总线

### 求解能力

- 硬约束：引脚冲突、定时器通道冲突、定时器 remap 全局冲突、UART/SPI/CAN/I2C 实例冲突、EXTI 线互斥、ADC 通道冲突
- 保留引脚：网页动态行操作，对 GPIO 和硬件复用统一生效
- PWM 分组：**每一行 = 一个频率组**，行内共用一颗定时器（同频），行间独立调速
- I2C 共享组：相同组名强制共用一条总线，不同组名强制分开，自动=求解器决定
- SPI 基础总线：一行 = 一条总线（共享 SCK/MISO/MOSI）+ 每个从机独立 NSS（GPIO 软件片选）
- 无方案诊断：提示定时器/UART/I2C/SPI/CAN/ADC/EXTI/GPIO 哪种资源不足

---

## 三、下一步计划（按优先级）

### M2：锁定引脚 + CubeMX 集成（进行中）

- [x] **锁定引脚重新求解**：锁定设置界面（下拉合法候选、排除已锁定、auto 占位、回显）
- [x] **导出/导入方案**：JSON 文件，导入后所有引脚自动锁定，支持增量加外设
- [x] **方案卡片展示**：折叠方框，前 12 套方案，点开看详情/导出
- [x] **生成 CubeMX 配置步骤**：方案卡片内嵌 CubeMX 步骤清单，Markdown 导出也包含
- [x] **导出多格式**：JSON（可导入）/ Markdown（看）/ CSV（Excel）
- [ ] **引脚图可视化**：网页画 LQFP48 引脚图，按外设颜色高亮，冲突红显

### M3：MSPM0G3507 天猛星板

- [ ] 用户整理天猛星板原理图/引脚引出表
- [ ] 手工录入 `chips/MSPM0G3507.json`（PINCM 灵活复用建模）
- [ ] 生成 TI SysConfig 配置步骤

### M4：智能化与效率

- [ ] 电赛场景模板一键套用（智能车 / 无人机 / 电源题）
- [ ] 方案保存/命名/对比
- [ ] 软件模拟建议：硬件不够时自动提示"这路可软件 I2C / 软件 PWM / GPIO 模拟编码器"

### M5：F407 支持（最后）

- [ ] 解析 CubeMX 芯片数据库自动生成引脚库
- [ ] 新增 STM32F407 系列

---

## 四、项目结构与维护方法

```
F:\pin_configuration_assistant\
├── app.py                  # 网页版入口（NiceGUI）
├── cli.py                  # 命令行入口
├── solver.py               # 求解引擎（Chip / Solver / 诊断 / 请求构建）
├── requirements.txt        # nicegui
├── README.md               # 使用说明
├── ROADMAP.md              # 本文件
├── .gitignore
├── chips\
│   └── STM32F103C8Tx.json  # 芯片引脚/复用数据库
└── peripherals\            # 外设模板（一个 JSON 一个外设）
    └── *.json
```

### 如何新增一个外设模板

在 `peripherals/` 下新建 JSON，例如 `my_sensor.json`：

```json
{
  "id": "my_sensor",
  "name": "我的传感器",
  "icon": "📡",
  "description": "一句话说明",
  "requests": [
    {"type": "uart", "role": "串口", "count": 1},
    {"type": "gpio", "role": "使能", "count": 1}
  ]
}
```

支持的 type：`uart` / `uart_tx` / `uart_rx` / `i2c` / `spi` / `spi_bus` / `can` / `timer_enc` / `timer_pwm` / `timer_pwm_exclusive` / `adc` / `exti_gpio` / `gpio`

### 如何新增一颗芯片

在 `chips/` 下新建 JSON，结构照抄 `STM32F103C8Tx.json`：
- `pins`：每个引脚的 `gpio` / `exti` / `adc` / `notes`
- `peripheral_groups`：UART / I2C / SPI / CAN / TIMER 的候选组（含 remap）
- `reserved_pins_by_default`：默认保留引脚
- `penalty_pins`：不推荐但可用的特殊脚（备注会显示）

---

## 五、日常运行命令

```bat
cd /d F:\pin_configuration_assistant
python cli.py                        # 命令行版
python app.py                        # 网页版，然后浏览器打开 http://127.0.0.1:8080
```

---

## 六、Git 推送备忘（待执行）

**最简单方式**：双击 `F:\pin_configuration_assistant\git_push.bat`，脚本已内置仓库地址，直接运行即可。

仓库地址：`https://github.com/tiantangc/pin_configuration_assistant`

前提：已安装 Git（https://git-scm.com/download/win），已在 GitHub 建好空仓库（不要勾选 README/.gitignore/license）。

---

## 七、已知注意事项

- F103C8T6 引脚数据为**手工录入**，正式使用前建议对照 CubeMX 或数据手册抽查
- `stepper_motor_variable.json` 是历史模板，已标记 `hidden`，列表不显示，保留文件仅作兼容
- 定时器建模：同一 TIM 的 remap 全局唯一；`timer_pwm_exclusive` 独占整颗定时器
- 网页默认保留 PA13/PA14（SWD）、PB2（BOOT1）、PD0/PD1（晶振），可手动删除
