# Mito Data Studio 文档

这是整个仓库的文档入口。新组员先理解标注流程，再学习代码；使用已有实验室实例不需要安装开发环境。
入门导读使用中文，按钮和代码名称保留英文，详细工程参考目前以英文为主。

## 按你的任务开始

| 我想做什么 | 从这里开始 | 下一步 |
| --- | --- | --- |
| 第一次加入项目 | [新组员入门](getting-started/README.md) | [术语表](getting-started/glossary.md) |
| 学会使用软件 | [使用手册](user-guide.md) | 按角色选择章节 |
| 知道 Measurements 在算什么 | [测量操作](user-guide/09-measurements.md) | [方法和实现](engineering/measurements.md) |
| 看懂代码、准备开发 | [代码地图](engineering/code-map.md) | [一个功能如何贯穿前后端](engineering/feature-walkthrough.md) |
| 做第一个小改动 | [第一次贡献](getting-started/first-contribution.md) | [开发环境](development.md) |
| 安装或维护服务 | [运维入口](operations/README.md) | 选择 Docker 或已有服务器流程 |
| 准备发布或写论文 | [发布检查](release/checklist.md) | [研究材料](research/README.md) |

## 文档树

```text
docs/
  index.md                  总入口
  overview.md               产品范围与角色
  getting-started/           新组员路线、术语、第一次贡献
  user-guide.md             使用手册入口
  user-guide/               按界面任务划分的操作说明
  engineering/              代码地图、架构、数据、算法、测量实现
  development.md            开发环境和日常命令
  product-invariants.md     改代码必须保留的产品约定
  operations/               安装、生产维护、参考硬件
  release/                  发布检查和带日期的验证记录
  research/                 Methods、研究证据、可复现性
  attribution.md            第三方代码与归属
```

旧 `documentation/` 和部分旧 `docs/*.md` 只保留迁移入口，不再维护第二份正文。
代码、迁移和测试决定实际行为；验证记录只证明对应日期和版本，不能代表后续版本自动通过。

## 专题参考

- 工程：[根目录文件说明](engineering/root-files.md)、[架构](engineering/architecture.md)、[数据与存储](engineering/data-and-storage.md)、[AI 与算法](engineering/ai-and-algorithms.md)、[测量实现](engineering/measurements.md)、[审计与改进建议](engineering/software-audit.md)。
- 发布：[带日期的验证历史](release/validation-history.md)、[第三方归属](attribution.md)。

## 维护文档

新增操作说明放在 `user-guide/`；内部设计放在 `engineering/`；部署步骤放在 `operations/`。
每个主题保留一份完整正文，其他页面链接过去。改变功能时同步更新对应说明；不要在文档中填写真实密码、token 或生产数据。

仓库根目录执行 `python scripts/docs/check_links.py` 检查文档链接。
贡献规则、许可证、安全政策仍在根目录：[CONTRIBUTING](../CONTRIBUTING.md)、[LICENSE](../LICENSE)、[SECURITY](../SECURITY.md)。
