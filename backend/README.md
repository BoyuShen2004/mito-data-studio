# Backend 导读

Django 负责权限、元数据、标注保存、审核和后台计算；体素文件存储与数据库分开。

1. 从 [API 路由](config/urls.py) 找到你关注的请求。
2. 进入对应 app 的 API、service、model 和相邻测试。
3. 用 [Measurements 示例](../docs/engineering/feature-walkthrough.md) 实际追踪一次。

完整的 [app 职责地图](../docs/engineering/code-map.md)、[数据契约](../docs/engineering/data-and-storage.md)和[开发命令](../docs/development.md)在统一文档树中。

完整 Django 测试从本目录运行 `python manage.py test --noinput`，并检查测试数量不是零。
先配置独立开发/测试数据库与数据目录，不能连接生产。迁移文件记录历史，不要为了整理文件而移动或重排它们。
