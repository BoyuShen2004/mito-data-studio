# 安装与运维入口

只使用实验室已有实例的组员不需要部署，先读[入门指南](../getting-started/README.md)。

| 场景 | 文档 |
| --- | --- |
| 新机器运行完整服务 | [Docker](docker.md) |
| 个人电脑改代码、热更新 | [开发环境](../development.md) |
| 按 CPU/GPU 条件配置开发部署 | [硬件适配](hardware-adaptive.md) |
| 维护当前实验室生产主机 | [生产主机运行手册](production-host.md) |
| 了解曾使用的硬件 | [参考硬件记录](reference-hardware.md)，不是最低配置要求 |

`docker-compose.yml` 是完整服务；`docker-compose.dev.yml` 只启动数据库；
`docker-compose.dev-stack.yml` 是完整开发栈。先确认使用哪一个，不要互换执行。

生产更新前检查依赖、迁移、备份和回滚方案。生产配置、数据库与 `MITO_DATA_ROOT` 保持独立，
源代码更新不等于把开发目录整体复制到生产。测量功能需要 dispatcher 接受 `measure_mito` 作业，
只接受 `build_pyramid` 的旧服务配置不会执行测量。

发布证据与工程测试见[发布检查](../release/checklist.md)和[验证历史](../release/validation-history.md)。
