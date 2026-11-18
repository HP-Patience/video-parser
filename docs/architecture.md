# 项目架构

## 源码布局

- `backend/video_parser/cli.py`：命令行适配，不包含平台实现。
- `backend/video_parser/web/app.py`：ASGI 应用、静态资源、健康检查。
- `backend/video_parser/web/routes.py`：工作台 HTTP 协议与会话管理。
- `backend/video_parser/web/security.py`：本机客户端、Host、Origin、请求类型校验。
- `backend/video_parser/services/videos.py`：解析、清晰度、下载、媒体校验及应用操作。`VideoRequest` 为与 argparse/HTTP 无关的请求参数。
- `backend/video_parser/services/library.py`：资源归档和文件访问保护。
- 其他 `services/` 模块：历史导入、画面分析、本机字幕与转写桥接。
- `backend/video_parser/platforms/`：平台工厂、四个平台解析器（小红书、抖音、哔哩哔哩、YouTube）、平台专用辅助代码和签名 JS。
- `backend/video_parser/core/`：按仓库标记定位路径、读取配置和日志设置。
- `frontend/src/features/`：library / subtitles / transcription / settings。
- `frontend/src/components/`、`lib/`、`styles/`：跨功能组件、接口与下载工具、样式。

## 依赖方向

```text
React → HTTP → web ─┐
                   ├→ services → platforms / core
CLI ───────────────┘
```

Web 和业务层不导入命令行适配器。命令行解析器只把选项转换为 `VideoRequest`，负责 stdout/stderr 和退出码。原业务操作先整体提取，不引入无实际需求的 repository/domain 层。

`services/history_import.py` 和 `services/analysis.py` 暂保留其辅助命令行入口，以 `python -m` 执行；它们不反向依赖主 CLI。这两个入口进一步分离不属于本次迁移。

## 工作台视频预览

解析接口沿用现有项目目录与封面归档流程。没有本地归档的视频，工作台从解析返回的标准来源链接生成受支持平台的官方播放器地址，由浏览器向平台请求视频流，不经过应用的媒体代理，也不写入项目。手动下载归档后，工作台优先使用本地播放器。

## 配置与数据边界

项目采用源码仓库中的 editable 安装。`core/paths.py` 根据 `pyproject.toml`、`configs/business_config.json` 和包目录标记查找仓库，不根据进程工作目录定位。

- `.env` 和 `configs/business_config.json` 原地保留。
- `outputs/<平台>/<项目>/` 是持久项目成果库；`outputs/transcriptions/` 保留独立转写结果。
- `.local/work/cache/`、`.local/work/logs/`、`.local/work/artifacts/` 分别保存缓存、日志、开发证据；模型在 `runtimes/models/`，工具环境原位保留。
- 历史任务路径仅在读取时重定位，不批量改写任务日志、聊天正文和素材。资料库项目 ID、原字幕与译文文件保持。
- `VIDEO_LIBRARY_ROOT`、`BILI_SUBTITLE_ROOT`、`WHISPER_ROOT` 的原有语义保留。
- `frontend/dist/` 仍由 FastAPI 提供。
- 签名 JS 随 Python 包配置为 package data。（快手解析器已于 2026-10-14 删除。）

日志路径现在固定在项目 `.local/work/logs/`，不再受从哪个目录启动影响。

## 兼容边界

根目录 `cli.py` 和 `api.py` 是薄兼容入口，分别转到包内 CLI 和同一个 FastAPI 应用。根部 CMD 入口与 Skill 保持，PowerShell 实现位于 `scripts/`，参数传递方式不变。旧的内部 Python 模块导入路径不作为兼容 API，仓库内调用已迁移。

不改变 `/api/workspace/` 契约、默认端口、资源格式、拒绝覆盖及本机同源校验。未重新引入 Flask、Gradio 或数据库服务。
