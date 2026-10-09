# 9. Measurements：测量已有标注

入口是 **Project → Measurements**。先选择 Volume；Manager 可以发起测量，已有 volume 访问权限的成员可以读取结果和导出 CSV。

## 操作顺序

1. 选择 Volume，等待读取 physical voxel size。已登记值优先，缺失轴再从支持的原始文件元数据读取。
2. 核对 Z、Y、X，单位是 **nm**。缺失时从采集记录取得实际值再输入；不能用猜测值代替。手动修改后按 **Save voxel size**。
3. 选择 **Official label** 或 **Saved working draft**。后者不包含浏览器中尚未 Save 的编辑。
4. 按 **Run measurements**，等待后台作业完成。
5. 查看每个 label ID 的结果，或导出 CSV。更换 Volume 或 source 会切换对应的结果。

完整的自动读取值可以直接用于测量；读取元数据不会自动改写已登记值。
测量既不保存标注，也不提交或审批任务。

Official label 指当前正式标签引用：可能是初始登记标签、审核通过的快照，
也可能是[撤回团队分配](03-people-and-assignment.md#working-team-changes-and-withdrawal)时提升的已保存草稿。
名称不保证已通过审核。提交快照不能直接选为测量来源；必须先通过审批安装，或测量已有正式/工作文件。
同一 Volume 同时只允许一个 queued/running 测量，另一个 source 也不能并行发起。
如果输入在执行前或执行中改变，作业失败且不发布部分结果；确认 Save 完成再重跑。

## 结果代表什么

- Voxel count：该 ID 包含的体素数量。
- Volume（µm³）：数量乘以每个体素的实际体积。
- Skeleton cable length（µm）：骨架所有边的总长度，不是对象两端的直线距离。

例如 Z/Y/X 为 30/16/16 nm，100 个体素的体积是
`100 × 0.03 × 0.016 × 0.016 = 0.000768 µm³`。
这只是单位换算示例，不是推荐填写的体素尺寸。

测量覆盖整个 label volume，不按 ROI 或任务范围裁剪。同一 ID 的区域合并统计；
少于 100 体素的连通分量计入体积，但不参与骨架长度，因此长度 0 不一定表示体积 0。

## 跑不了或结果过时怎么办

| 现象 | 检查 |
| --- | --- |
| 缺少 spacing / 按钮不可用 | 三个轴是否都有真实的正数值；手动修改是否已保存；账号是否为 Manager |
| 没有可用 label | 选择的 source 是否存在；Saved working draft 是否已经保存 |
| 一直 queued / Measurement failed | 请维护者检查 dispatcher 是否处理 `measure_mito` 且安装 kimimaro；当前 Docker 镜像缺少此依赖，也不自动启动 dispatcher；不要反复点运行 |
| 超出 crop 限制 | Web 每个对象的带边界裁剪上限为 8,000,000 体素；交给维护者评估离线测量 |
| Outdated result | label 或 spacing 已变化；确认来源后重新运行 |

更多方法、限制和离线命令见[测量实现](../engineering/measurements.md)。
返回[使用手册](../user-guide.md)。
