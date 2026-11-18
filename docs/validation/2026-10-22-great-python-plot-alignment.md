# GREAT_Python 绘图脚本参考与迁移核对

日期：2026-10-22。用户要求以 `F:\GREAT_Python` 下现有绘图脚本为参考，调整 GREAT Agent 自动后处理。原工程保持只读，GREAT Agent 运行时无需访问或导入该工程。

## 选用的参考

主参考为 `plot/pppflt_new/ppp_enu_plot.py`（ENU、统计、方案顺序）和 `plot/pppflt_new/ppp_cdf_plot.py`（汇总与后收敛 CDF），补充查阅 `plot/pppflt_new/3files_compare_enu.py` 与 `plot/flt_compare/3files_compare_enu_reset4-24%4.py`。

实际核对的读取、坐标和统计函数位于 `plot/dependency/gnss_io/gnss_flt_io.py`、`plot/dependency/gnss_tool/gnss_cord_tool.py`、`plot/dependency/gnss_tool/gnss_math_tool.py`。

| 主参考文件 | SHA256 |
|---|---|
| ppp_enu_plot.py | ED3CD8047DE736C458708B410E5FAFBF7EC61FCCFA54B9F5AFBCA57585F0252C |
| ppp_cdf_plot.py | 6FAA75E9DE061D95C2B1651422E67AAB2E0D614DB537198F79631F86D6FC8F0F |

## 已对齐的约定

- 同日、同站的静态 DF Float 最后有效历元 ECEF XYZ 为共同参考。
- ENU 使用参考位置的 WGS84 大地纬经度，不用地心纬度。
- East、North、Up 独立三行；横轴是 GPS 当日小时数，概览主刻度间隔 2 小时。
- 概览显示范围 ±0.3 m、0.1 m 纵轴刻度；首小时另提供完整误差范围，保留初始化过程。
- Times New Roman，向内刻度，灰色虚线网格，点状而非跨缺口连线，300 DPI。
- 方案对应颜色：DF_Float #086CA2、DF_Fixed #2CA02C、TF_Fixed #D62728；只有当前真实存在的方案会被绘制。
- RMS = sqrt(mean(error²))，STD = sqrt(mean((error - mean(error))²))，即 ddof=0。
- Fixed / (Fixed + Float) 为固定率；取 FLT 第 17 列 AmbStatus，不取第 18 列 Quality。
- 输出 RMS_E/N/U/3D、STD_E/N/U、FixRate(%)、Conv(min)，保留原来的 JSON 和逐历元 CSV。
- 收敛时间 CDF；RMS_E/N/U/3D、STD_E/N/U、固定率 CDF；后收敛 |E|/|N|/|U|/水平/3D CDF，共 14 张。

## 明确保留的差异

1. 原脚本默认连续 5 历元达标。用户已在本对话明确确认连续 10 分钟，因此默认仍为 600 秒，可配置，窗口首尾时间差必须满足 600 秒。
2. 原脚本用序列下标乘 30 秒估算收敛时间。这里使用实际时间戳并重置跨缺口窗口，收敛时间从计划 GPS 当日零点计算。
3. 原脚本用全段时长表示未收敛。这里保留 null/空值/N/C；未收敛样本不会变成一个成功收敛的时间点，收敛 CDF 的分母仍包含这些测站。
4. 原 CDF 会先截断样本到显示范围，再做插值和平滑。这里使用精确经验 CDF，不改变原始概率质量；超出坐标轴的样本仍在分母中，单样本也可绘制。
5. 原脚本的后收敛历元索引在没有真正收敛时可能回落到 0。这里仅收集确实找到合格窗口的测站，并按实际时间筛选。
6. 原字体权重 560 在本机 Times New Roman 下会回落到 700 并打印警告；直接使用可用的 bold 字重，避免缺字重警告。
7. 原脚本写入 Excel，并允许直接从其目录批量处理多天、多方案；当前项目沿用 attempt 隔离、JSON/CSV 和数据库文件登记，尚未增加 Excel 导出或多方案同图叠加入口。

## 真实结果数值核对

对实验 `476c79c3-5452-4967-afc7-0bafad975436` 的 15 站、43,160 个历元，直接调用原工程 `readGreatFltFile_v2`、`ecef2pos`、`ecef2enu`、`get_rms` 和 `get_standard_deviation` 与迁移实现逐项对比。仅导入函数，不运行其批处理主入口。

| 检查 | 结果 |
|---|---|
| 15 站历元数量 | 全部一致 |
| 固定率 | 全部一致 |
| ENU 最大绝对差 | 8.9484e-14 m |
| RMS 最大绝对差 | 2.6091e-15 m |
| STD 最大绝对差 | 2.6230e-15 m |

数值差异在浮点舍入范围内。收敛时间使用用户新确认的口径，因此不要求与原脚本“连续 5 历元”的数值一致。

## 自动流程与范围

worker 在静态 DF Float 解算完后自动调用同一套分析函数；`great-agent analyze` 可对已完成的 experiment 单独重绘。新增 CDF 也登记到结果列表，可以通过现有下载接口访问。完整 15 站单日分析现在生成 48 个产物（30 张图、17 份 CSV、1 份 JSON），加原有 15 个 FLT 与 solver.log，共 64 条结果登记。

本轮只重做已有 FLT 的后处理，不重新启动 GREAT。原始 FLT、已执行 XML、外部绘图脚本和 GNSS 产品均保留。
