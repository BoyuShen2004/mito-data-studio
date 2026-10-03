# 配置模板

只保存示例配置。实际配置仍放在仓库根目录的 `.env`、`.env.docker` 或 `.env.docker.dev`，并由 Git 忽略。
不要把真实密钥或生产配置放进此目录。

从仓库根目录选择**一种**环境，只在目标文件不存在时复制：

| 场景 | 模板 → 本地文件 | 对应入口 |
| --- | --- | --- |
| Conda + 本地 Django/Vite | `config/env/host.env.example` → `.env` | `make setup` / `make dev` |
| Docker 完整部署 | `config/env/docker.env.example` → `.env.docker` | `docker compose --env-file .env.docker …` |
| Docker 完整开发栈 | `config/env/docker-dev.env.example` → `.env.docker.dev` | `make docker-dev-up` |

三份模板面向不同运行方式，不能合成同一套默认值。它们的字段和值未因目录整理改变。
完整步骤见[开发文档](../../docs/development.md)和[Docker 文档](../../docs/operations/docker.md)。
