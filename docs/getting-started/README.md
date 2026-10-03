# 新组员从这里开始

Mito Data Studio 把显微图像、线粒体标注、任务分配、审核和测量放在同一个网页工作台里。
图像告诉你“看到了什么”；标注用整数 ID 表示“哪些体素属于同一个对象”。软件帮助团队协作，不会自动保证标注或测量在生物学上正确。

## 第一天：先学会使用

1. 向组内负责人取得 **dev 地址、你的账号和练习任务**。不要在真实生产任务上练习，也不需要自己部署一套服务。
2. 阅读[术语表](glossary.md)，能区分 image、label、volume、task、ROI。
3. 按自己的角色阅读下表，然后在练习任务中完成一次操作。没有相应权限的按钮不会显示。

| 角色 | 第一次要完成什么 | 阅读 |
| --- | --- | --- |
| Annotator（标注者） | Home → Assigned to me → 任务；先 View，再 Annotate；选择 label，修改少量体素并 Save | [查看图像](../user-guide/04-viewer.md)、[标注工具](../user-guide/05-annotation-tools.md) |
| Manager（管理者） | 打开待审核任务的 Review，查看提交，再作审核决定 | [分配任务](../user-guide/03-people-and-assignment.md)、[审核](../user-guide/07-submit-and-review.md) |
| Requester（提出需求者） | 创建项目，准备数据路径，理解注册和审批 | [项目与数据](../user-guide/02-projects-and-data.md) |

练习中的 **Submit** 和 **Approve** 也会改变任务状态，先和负责该练习的组员约定好。
如果看不到任务，先核对账号角色、项目访问权限和任务分配；它们不是一回事。

## 先记住这条数据流

```text
原始图像（只读） + 初始 label（可选）
                    ↓
浏览器中的待保存编辑 → Save → 工作草稿
                                  ↓ Submit
                              提交快照
                                  ↓ Manager approves
                              正式 label
```

- **Save** 保存草稿，不是提交审核。**Submit** 创建用于审核的快照。
- **Approve** 才会把选定快照变成正式结果；被退回的任务可以继续修改并重新提交。
- AI proposal 是候选结果，需要检查。接受 proposal 和保存标注是不同操作。
- Measurements 读取正式 label 或已保存草稿，测量本身不修改标注。
- 公共分享只读。没有体素尺寸时不能猜测实际长度或体积。

## 接下来按目标学习

- 继续使用软件：[完整操作流程](../user-guide/workflows.md)、[Measurements](../user-guide/09-measurements.md)。
- 看懂组里做了什么：[产品范围](../overview.md)、[代码地图](../engineering/code-map.md)。
- 开始写代码：[第一次贡献](first-contribution.md)，再读[功能追踪示例](../engineering/feature-walkthrough.md)。
- 写论文或报告：[研究材料](../research/README.md)。功能存在、测试通过和科学有效性是三种不同证据。

## 你理解了吗？

能向同学解释：Save 与 Submit 的区别；ROI 与实例 label 的区别；为什么不同 ID 代表不同对象；
为什么未知 voxel size 不能填 1 来“先跑通”；遇到问题时该提供任务 ID、操作步骤和报错，而不是发送账号密码。
