# 代码地图：从页面找到实现

先读[新组员入门](../getting-started/README.md)。不需要从第一行开始读整个仓库；先选一个功能，沿着页面、API、后端服务和测试追踪。

## 顶层目录

| 路径 | 里面是什么 | 新人什么时候看 |
| --- | --- | --- |
| [frontend/](../../frontend/) | 浏览器界面、交互、前端测试 | 改页面、工具或显示 |
| [backend/](../../backend/) | Django API、权限、数据模型、算法、后台作业 | 理解保存、审核、测量如何完成 |
| [docs/](../index.md) | 唯一的完整文档树 | 使用、开发、运维、研究 |
| [documentation/](../../documentation/README.md) | 旧链接迁移入口 | 不在这里新增正文 |
| [scripts/](../../scripts/) | 本地开发与文档检查脚本 | 重复执行开发检查 |
| [ops/](../../ops/) | Docker、staging、production 和发布工具 | 由维护者部署时使用 |
| [vendor/](../../vendor/) | 可选模型资源及第三方资产 | 使用 AI 功能或核对归属 |
| [manage.py](../../manage.py)、[Makefile](../../Makefile) | 命令入口和常用命令集合 | 环境配置完成后使用 |
| [environment.yml](../../environment.yml)、[requirements-release.txt](../../requirements-release.txt) | 开发环境 / 带哈希的发布依赖锁 | 不把已有开发环境当作固定发布环境 |
| [frontend/package.json](../../frontend/package.json)、[frontend/package-lock.json](../../frontend/package-lock.json) | 前端命令 / 依赖锁 | 查 npm 命令和精确依赖 |

本地还可能看到 `var/`、`data/`、`logs/`、`venv/`、`node_modules/`、`dist/`：它们通常是运行数据、环境或构建产物，不是需要学习或搬动的源代码。
真实 `.env` 不入库；`.env.*.example` 是配置模板。

## 先理解三层

前端是浏览器中的 React/TypeScript 界面；后端是服务器上的 Django/Python 程序。
API 把它们连接起来。数据库记录用户、任务和文件位置，显微图像及标注数组保存在文件存储中。
修改页面文字通常只涉及前端；修改谁能保存、保存到哪里或如何计算，则需要理解后端约束。

## 前端怎么读

| 顺序 | 文件或目录 | 作用 |
| --- | --- | --- |
| 1 | [main.tsx](../../frontend/src/main.tsx) → [AppRoutes.tsx](../../frontend/src/routes/AppRoutes.tsx) | 启动应用，按 URL 找到页面 |
| 2 | [pages/](../../frontend/src/pages/) | Home、ProjectDetail、TaskDetail、Viewer 等整页组合 |
| 3 | [components/](../../frontend/src/components/) | 可复用列表、表单、审核框和测量面板 |
| 4 | [api/](../../frontend/src/api/)、[types/](../../frontend/src/types/) | 请求后端以及数据类型 |
| 5 | [features/viewer/](../../frontend/src/features/viewer/) | 图像画布、编辑状态、工具交互；初学者按功能找，不从巨型 Canvas 文件顺读 |
| 6 | 相邻的 `*.test.tsx` / `*.test.ts`、[e2e/](../../frontend/e2e/) | 查看行为例子和回归约束 |

## 后端怎么读

从 [config/urls.py](../../backend/config/urls.py) 找 API，再读对应 app 的 API、service、model 和测试。
不同 app 的文件划分略有差别，不是每个功能都只在一个 `services.py` 中。

| App | 职责 |
| --- | --- |
| [accounts/](../../backend/accounts/) | 用户角色、机构、团队、访问和审计 |
| [projects/](../../backend/projects/) | 项目、dataset、成员和分享 |
| [volumes/](../../backend/volumes/) | 图像注册、元数据、ROI、金字塔和切块读取 |
| [annotation/](../../backend/annotation/) | 标注任务、编辑、提交审核、难例、AI、测量 |
| [processing/](../../backend/processing/) | 持久化后台作业及 dispatcher |
| [core/](../../backend/core/) | 公共类型、存储边界、安全和部署辅助 |

`models.py` 描述数据库对象，`serializers.py` 描述接口数据，`migrations/` 记录数据库演进。
体素文件不直接塞进这些数据库表；位置、归属和生命周期见[数据与存储](data-and-storage.md)。

## 按问题定位

| 我要理解 | 第一组文件 |
| --- | --- |
| Home 中的任务与 Review 按钮 | [HomePage](../../frontend/src/pages/HomePage.tsx)、[WorkList](../../frontend/src/components/WorkList.tsx) |
| 如何测量、为什么要 spacing | [Measurements 功能追踪](feature-walkthrough.md) |
| 草稿、提交和正式标签 | [label_paths.py](../../backend/annotation/label_paths.py)、[annotation services](../../backend/annotation/services.py)、[审核手册](../user-guide/07-submit-and-review.md) |
| 查看图像为何可以很快 | [pyramid/](../../backend/volumes/pyramid/)、[架构](architecture.md) |
| 哪些行为不能顺手改掉 | [产品约定](../product-invariants.md)、[AGENTS.md](../../AGENTS.md) |

运行时代码目录保持稳定，避免为了整理目录破坏导入、迁移、模型路径或服务配置。
内部大模块的拆分建议单独记录在[软件审计](software-audit.md)，不是本次文档重组的内容。
