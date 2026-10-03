# 根目录文件：哪些必须保留？

根目录保留工具自动发现的入口和项目政策；模板归入 `config/env/`，发布依赖归入 `requirements/`。
文件数量不等于重复程度，同一主题的不同文件可能服务于不同工具。

| 文件 | 保留原因 |
| --- | --- |
| `README.md` | 仓库首页，指向使用与开发文档 |
| `LICENSE`、`THIRD_PARTY_NOTICES.md` | 第一方授权状态和第三方许可证义务不同，不能合成一个许可证 |
| `SECURITY.md` | 安全问题报告入口，托管平台可直接发现 |
| `CONTRIBUTING.md`、`CHANGELOG.md` | 贡献流程和版本变化，读者及用途不同 |
| `AGENTS.md` | 编码 agent 必须遵守的项目约束 |
| `.gitignore`、`.dockerignore` | 分别控制 Git 跟踪和 Docker 构建上下文，语法及用途不同 |
| `.gitattributes` | Git LFS 模型权重规则，删除可能导致大文件处理出错 |
| `.editorconfig` | 编辑器通用格式规则 |
| `pyproject.toml` | 可选 pytest/coverage 工具配置，不是另一份依赖锁 |
| `manage.py` | 仓库根目录的 Django 命令入口，脚本和文档依赖它 |
| `Makefile` | 开发、构建和检查命令的统一入口 |
| `environment.yml` | Conda 开发环境，包含 Python、Node 和 CUDA |
| `Dockerfile` | 三阶段容器构建入口 |
| `docker-compose.yml` | 完整服务部署 |
| `docker-compose.dev.yml` | 仅本地开发数据库 |
| `docker-compose.dev-stack.yml` | 完整开发服务，使用独立服务身份和存储卷 |
| `.env`（不跟踪） | 当前本地配置；不是可随意删除的临时文件 |

三个 Compose 文件虽然有相似段落，但数据库身份、卷、端口和开发行为不同。
本次保留独立配置，避免整理文件时误用数据库或改变登录与功能开关。

## 已归档到专门目录的文件

| 旧路径 | 新路径 |
| --- | --- |
| `.env.example` | [config/env/host.env.example](../../config/env/host.env.example) |
| `.env.docker.example` | [config/env/docker.env.example](../../config/env/docker.env.example) |
| `.env.docker.dev.example` | [config/env/docker-dev.env.example](../../config/env/docker-dev.env.example) |
| `requirements-release.in` | [requirements/release.in](../../requirements/release.in) |
| `requirements-release.txt` | [requirements/release.txt](../../requirements/release.txt) |

原路径不保留重复副本。仓库内的脚本和文档已更新；你自己的外部脚本若引用旧路径，需要同步修改。
真实 `.env` 的位置、内容和运行时读取方式不变。

`make help` 列出开发入口。`make build` 保留原有开发构建行为；正式生产包明确使用 `make build-production`。
`make test-backend` 从 `backend/` 执行，避免根目录默认发现零个测试；检查输出的测试数。
