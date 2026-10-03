# 第一次贡献：从一个小改动开始

先读[代码地图](../engineering/code-map.md)和[产品约定](../product-invariants.md)。
建议第一次修正文档或一个已有页面的显示问题，不从重写画布、账号权限或数据库迁移开始。

## 准备独立开发环境

1. 先确认自己操作的是个人开发 checkout，而不是实验室正在使用的 dev/production 部署目录。
2. 按[开发环境指南](../development.md)安装依赖，使用独立数据库和 `MITO_DATA_ROOT`。不要复制生产 `.env`。
3. 在干净工作区更新 main，再创建自己的分支，例如：

   ```bash
   git switch main
   git pull --ff-only
   git switch -c docs/explain-measurements
   ```

   `git status` 若有未完成改动，先处理或保存自己的工作，不要用 reset 丢弃它。

## 找文件 → 小改动 → 验证

- 文档改动：正文放在对应 `docs/` 子目录，从索引链接过去；不要修改旧迁移入口中的正文。
- 页面改动：从路由找到 page，再找到 component。阅读相邻测试，保留按钮行为和数据状态的含义。
- 后端改动：从 URL 找 API，再找 service 和测试。前端隐藏按钮不等于后端授权。

在已经配置好的个人开发环境、仓库根目录运行：

```bash
python scripts/docs/check_links.py
npm run typecheck --prefix frontend
npm test --prefix frontend
```

后端示例使用临时 SQLite 和临时文件目录，避免连接共享数据库。先激活项目 Python 环境：

```bash
mito_test_dir=$(mktemp -d)
(
  cd backend
  MITO_DB_ENGINE=sqlite MITO_SQLITE_NAME="$mito_test_dir/test.sqlite" \
  MITO_DATA_ROOT="$mito_test_dir/data" \
  python manage.py test annotation.test_measurements --noinput
)
```

这只是一个模块的快速检查，不能替代完整后端测试，特别是 PostgreSQL 并发行为。
完整测试应从 `backend/` 运行 `python manage.py test --noinput`，连接自己的测试环境，并检查发现的测试数量不为零。
测量测试需要安装测量依赖；需要哪些检查由实际改动决定，文档改动不必运行 GPU 算法测试。

生产前端检查使用 `npm run build:production --prefix frontend`。
`npm run build` / `make build` 会保留开发账号构建标志，不要把它们的产物直接当作生产包。
浏览器回归入口是 `frontend/playwright.scientific.config.ts`；当前配置依赖此主机的 `/snap/bin/chromium`，换电脑需先配置可用浏览器。

## 交给同学审查

执行 `git diff --check` 和 `git diff`，确认没有夹带数据、密钥或不相关改动。
PR 写清楚：原来的问题、改后的行为、实际运行的检查，以及没验证的部分。
文档需要能让没有参与聊天的同学独立读懂；测试未运行时不要写“已通过”。

完整规则见 [CONTRIBUTING](../../CONTRIBUTING.md)。提交 PR 和部署服务是两件事，合并本身不会自动证明生产可安全更新。
