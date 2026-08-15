# 单片机引脚配置辅助助手

电赛电控专用：告诉它你用哪些外设，它给出 STM32F103C8T6 / MSPM0G3507 的最佳引脚分配方案。

GitHub 仓库：https://github.com/tiantangc/pin_configuration_assistant

作者：福州大学物理与信息工程学院 · 长江七号 tiantangc

---

## 网页版运行方法（推荐）

需要 Python 3.8+。

```bat
cd /d F:\pin_configuration_assistant
pip install nicegui
python app.py
```

浏览器打开 **http://127.0.0.1:8080** 即可使用。

## 命令行版运行方法

```bat
cd /d F:\pin_configuration_assistant
python cli.py                        # 内置示例场景
python cli.py --list                 # 查看支持的芯片和外设
python cli.py --scenario "visual:1,stepper_motor:3" --top 5
```

---

## 核心功能

### 1. 外设与引脚配置（网页版）

- **芯片切换**：标题栏下拉选择 STM32F103C8T6 或 MSPM0G3507（天猛星板）
- 动态增删外设行，每行带序号、数量、备注
- 外设类型下拉可选（共 21 个模板）
- 保留引脚动态增删，带备注（默认保留调试口、板级特殊脚、晶振脚）
- 设置页实时引脚预览图：锁定=橙、保留=红、空闲=浅灰，悬停看详情

### 2. 智能求解

- 硬约束：引脚冲突、定时器通道冲突、定时器 remap 全局冲突、EXTI 线互斥、UART/SPI/CAN/I2C 实例冲突、ADC 通道冲突
- **每一行 = 一个频率组**：行内 PWM 共用一个定时器（同频），不同行独立调速
- **I2C 共享组**：相同组名强制共用一条总线，不同组名强制分开
- **SPI 总线**：共享 SCK/MISO/MOSI，每个从机独立 NSS（GPIO 软件片选）
- **MSPM0 灵活复用**：UART/I2C/SPI/TIMER 信号跨引脚自由组合（区别于 F103 的 remap 组）
- 板级特殊脚评分优化：优先避开板载 LED、USB、JTAG、32.768k 晶振脚
- 无方案时输出具体原因（定时器/UART/I2C/SPI/CAN/ADC/EXTI/GPIO 哪种资源不足）

### 3. 锁定引脚

- 每行点「🔓 未锁定」进入锁定设置界面
- 下拉框只列该角色**合法且未占用**的候选
- 支持同角色多实例（auto 占位区分第几个）
- 锁定引脚在结果中标注「已锁定」

### 4. 方案展示与导出

- 方案折叠卡片，显示前 12 套，每套带得分
- 导出格式：**JSON**（可再次导入）/ **Markdown**（给人看）/ **CSV**（Excel 打开）
- 导入方案 JSON 后所有引脚自动锁定，可继续加外设增量分配

### 5. 配置步骤生成

- STM32F103C8Tx：生成「📋 CubeMX 配置步骤」（Pinout 选信号、外设模式、重映射提醒）
- MSPM0G3507：生成「📋 SysConfig 配置步骤」（CCS 中 PinMux 选引脚功能、ADD 外设、PWM/QEI/ADC/中断）
- Markdown 导出中也包含对应步骤

### 6. 引脚图可视化

- **芯片引脚图**（F103 LQFP48 封装）
- **板子排针图**：F103 最小系统板（2×20 排针）、MSPM0 天猛星板（4×20 排针）
- 按外设类型着色，工程图式引线标注每个引脚用途
- 支持查看大图、下载 SVG / PNG / JPG

### 7. 方案保存与软件模拟建议

- 保存/命名当前方案到 `saved_solutions/` 目录，随时加载恢复
- 对比两个已保存方案，列出相同引脚、差异、保留引脚差异
- 无方案时自动给出软件替代建议（软件 I2C / 软件 PWM / GPIO 模拟编码器）

---

## 外设模板（21 个）

**成品外设**：I2C屏幕、MPU6050、视觉模块、步进电机、小车电机、硬件编码器、GPIO模拟编码器、DBUS遥控器、舵机、SPI屏幕

**基础功能**：GPIO输入、GPIO输出、PWM输出、UART串口、UART仅TX、UART仅RX、I2C总线、SPI总线、ADC输入、EXTI中断输入、CAN总线

---

## 项目结构

```
pin_configuration_assistant/
├── chips/
│   ├── STM32F103C8Tx.json     # F103 引脚/复用/封装数据库
│   └── MSPM0G3507.json        # 天猛星板引脚/灵活复用/排针布局数据库
├── peripherals/               # 21 个外设模板（JSON）
├── solver.py                  # 求解引擎（约束/评分/诊断，兼容两种芯片格式）
├── cli.py                     # 命令行入口
├── app.py                     # 网页版入口（NiceGUI）
├── requirements.txt
├── README.md                  # 本文件
└── ROADMAP.md                 # 路线图与开发备忘
```

---

## 下一步计划

- [ ] 电赛场景模板一键套用（智能车/无人机/电源题）
- [ ] F407 支持（最后）

详见 ROADMAP.md。

