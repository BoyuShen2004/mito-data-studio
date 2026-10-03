# Python 发布依赖

- `release.in`：维护者编辑的直接依赖与版本约束。
- `release.txt`：生成的依赖锁，包含间接依赖和下载哈希；部署时使用。

两者不是重复文件。依赖变更后，从仓库根目录运行 `release.txt` 顶部记录的编译命令，再审查差异。
目录整理只更新路径，不升级或重新解析依赖。

在独立发布虚拟环境中安装：

```bash
uv pip install --python /path/to/release/venv/bin/python \
  --index-strategy unsafe-best-match --require-hashes -r requirements/release.txt
```

这里同时使用 PyPI 与 PyTorch 索引，版本和哈希由锁文件限定。
`environment.yml` 另外描述 Conda 开发环境，包括 Python、Node 和 CUDA；
`ops/docker/requirements-*.txt` 则用于 Docker 的 core/CPU/GPU 构建分层。
它们不是上述发布锁的同义副本，不能直接合并。参见[开发环境](../docs/development.md)。
