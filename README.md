# 单片机引脚配置辅助助手（M1 里程碑）

电赛电控专用：告诉它你用哪些外设，它给出 STM32F103C8T6 的最佳引脚分配方案。

## 网页版运行方法（推荐）

需要 Python 3.8+。

```bat
cd /d F:\pin_configuration_assistant
pip install nicegui
python app.py
```

然后在浏览器打开 **http://127.0.0.1:8080**，页面上选外设数量、填保留引脚、点“自动分配”即可。

## 命令行版运行方法

```bat
cd /d F:\pin_configuration_assistant
python cli.py
```

## 当前功能

- 内置 STM32F103C8T6（LQFP48）完整引脚复用数据库：UART / I2C / SPI / TIM1~TIM4 / ADC / EXTI / 重映射
- 内置 7 个电赛常用外设模板：
  - I2C屏幕（OLED / I2C LCD）
  - MPU6050
  - OpenMV（串口）
  - 步进电机（STEP/DIR/EN，多个步进自动共用一个定时器）
  - 小车电机（PWM + IN1/IN2）
  - 硬件编码器（定时器编码器模式）
  - GPIO 模拟编码器（EXTI）
- 硬约束检查：引脚冲突、定时器通道冲突、定时器 remap 全局冲突、EXTI 线互斥、UART/SPI 实例互斥
- 智能特性：I2C 从机自动共享总线、多路 PWM 自动成组到同一定时器、默认避开 SWD/BOOT 脚
- 输出 Top-N 方案 + 评分 + 资源占用面板

## 运行方法

需要 Python 3.8+，无需安装任何第三方库。

```bash
cd F:\pin_configuration_assistant

# 1. 看内置示例（你的痛点场景）
python cli.py

# 2. 自定义外设清单
python cli.py --scenario "i2c_screen:1,mpu6050:1,openmv:1,stepper_motor:3,car_motor:2,encoder_hw:1,encoder_gpio:1"

# 3. 保留额外引脚（比如同学已经占用 PA0）
python cli.py --scenario "openmv:1,stepper_motor:2" --reserve PA0,PA1

# 4. 查看支持的外设和芯片
python cli.py --list

# 5. 输出更多方案
python cli.py --top 5
```

## 项目结构

```
pin_configuration_assistant/
├── chips/
│   └── STM32F103C8Tx.json     # 芯片引脚/复用数据库
├── peripherals/
│   ├── i2c_screen.json         # 外设模板
│   ├── mpu6050.json
│   ├── openmv.json
│   ├── stepper_motor.json
│   ├── car_motor.json
│   ├── encoder_hw.json
│   └── encoder_gpio.json
├── solver.py                   # 求解引擎
├── cli.py                      # 命令行入口
├── app.py                      # 网页版入口（NiceGUI）
├── requirements.txt
└── README.md
```

## 下一步计划（M2）

- 引脚锁定后重新求解
- 生成 CubeMX 配置步骤清单
- 导出 Markdown/CSV 引脚表
- 芯片引脚图可视化高亮
