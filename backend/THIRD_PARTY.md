# 后端依赖与许可证

本表依据实际安装的发行包元数据及随包 LICENSE 文件整理。锁定版本见 `requirements.txt`；未复制完整开源后台项目或引入商业支付源码。项目自己的授权方式不由此表决定。

| 依赖 | 版本 | 上游许可证 | 用途 |
| --- | --- | --- | --- |
| Django | 5.2.17 | BSD-3-Clause | ORM、迁移、认证、后台 |
| djangorestframework | 3.18.1 | BSD-3-Clause | 只读 HTTP API |
| django-unfold | 0.108.0 | MIT | 后台页面和组件 |
| django-import-export | 4.4.1 | BSD-2-Clause | 导入预检、字段差异、导出 |
| python-dotenv | 1.2.1 | BSD-3-Clause | 本机环境配置 |
| asgiref | 3.12.1 | BSD-3-Clause | Django 依赖 |
| diff-match-patch | 20241021 | Apache-2.0 | 导入差异展示 |
| et_xmlfile | 2.0.0 | MIT | XLSX XML 读写 |
| openpyxl | 3.1.5 | MIT | XLSX 模板、解析与安全文本导出 |
| sqlparse | 0.6.0 | BSD-3-Clause | Django SQL 处理依赖 |
| tablib | 3.10.0 | MIT | 表格数据结构 |
| tzdata | 2026.4 | Apache-2.0（Python 包装；时区数据另附许可） | 时区数据 |

安装时各发行包会保留其 `.dist-info` 中的许可证、版权和相关 NOTICE。打包分发依赖时应一同保留这些文件；本表不是对原始许可证文本的替代。
