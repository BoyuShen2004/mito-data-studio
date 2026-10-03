# 追踪一个功能：Measurements

这个例子连接“网页按钮”和“后台计算”。先按[操作指南](../user-guide/09-measurements.md)理解用户看到什么，再打开下面的文件。

## 1. 页面与输入

[ProjectDetailPage.tsx](../../frontend/src/pages/ProjectDetailPage.tsx) 挂载 Measurements tab。
[ProjectMeasurements.tsx](../../frontend/src/components/ProjectMeasurements.tsx) 管理 volume 选择和 voxel size 表单；
[MitoMeasurements.tsx](../../frontend/src/components/MitoMeasurements.tsx) 管理 source、排队、状态和结果。

这里的 state 是浏览器状态，不等于数据库记录。切换 volume 时，要避免上一个异步请求覆盖新 volume 的结果。

## 2. 浏览器调用 API

[api/measurements.ts](../../frontend/src/api/measurements.ts) 通过公共客户端发送请求：

| 请求 | 意义 |
| --- | --- |
| `GET /api/volumes/:id/measurement-spacing/` | 读取可用的真实 spacing，不保存元数据 |
| `GET /api/volumes/:id/measurements/?source=official` | 获取这个来源最近的作业和结果 |
| `POST /api/volumes/:id/measurements/` | 请求排队；不在 HTTP 请求内完成骨架计算 |

在 [config/urls.py](../../backend/config/urls.py) 查这些路径，就能找到
[measurement_api.py](../../backend/annotation/measurement_api.py)。
后端仍然检查身份和权限，即使有人绕过前端直接发请求，也不能靠显示按钮获得权限。

## 3. 数据和计算

- [measurement_spacing.py](../../backend/annotation/measurement_spacing.py)：逐轴保留登记值，再补充可读取的文件元数据。未知仍是未知。
- [processing/models.py](../../backend/processing/models.py)：持久化作业状态。
- [processing/services.py](../../backend/processing/services.py)：dispatcher 领取作业，调用相应 runner。
- [measurement_jobs.py](../../backend/annotation/measurement_jobs.py)：选取输入、核对文件和 spacing 是否变化、保存派生结果。
- [measurements.py](../../backend/annotation/measurements.py)：逐 ID 统计体积并计算骨架长度。

存储中的 spacing 单位为 µm，测量引擎使用 nm。转换发生在明确的边界，不能因为表单显示 nm 就把原始数值直接存成 µm。
网页只排队而 dispatcher 未启动时，结果不会自己出现。中断恢复等已知改进见[审计记录](software-audit.md)。

## 4. 找到约束它的测试

| 测试 | 保护什么 |
| --- | --- |
| [ProjectMeasurements.test.tsx](../../frontend/src/components/ProjectMeasurements.test.tsx) | volume 选择、spacing 读取和保存 |
| [MitoMeasurements.test.tsx](../../frontend/src/components/MitoMeasurements.test.tsx) | 发起作业、状态和结果呈现 |
| [test_measurement_api.py](../../backend/annotation/test_measurement_api.py) | API 权限、排队、输入变化 |
| [test_measurement_spacing.py](../../backend/annotation/test_measurement_spacing.py) | 轴顺序、单位和缺失值 |
| [test_measurements.py](../../backend/annotation/test_measurements.py) | 已知合成形状的体积与长度 |

练习：沿代码说明“为什么打开页面不会开始计算”和“为什么未 Save 的编辑不会进入结果”。
先回答这两个问题，再尝试改计算功能。合成测试通过也不等于真实科研样本的生物学准确性已经得到验证。
