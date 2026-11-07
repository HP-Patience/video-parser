# README 图像资源

更新日期：2026-11-07。界面截图统一使用深色主题。

- `hero.svg`：自包含的黑白流程插画，不是软件截图。只用灰阶色值，提供 `title` / `desc`，不依赖远程字体、图片或脚本。
- `workspace.png`：视频工作台，深色，1440 × 1040。
- `workspace-empty.png`：未选择视频时的工作台初始状态，深色，1440 × 1040。
- `overview.png`：视频总览与资料提醒，深色，1440 × 1200。
- `transcription.png`：字幕来源与版本，深色，1440 × 1040。
- `learning.png`：分集学习与学习材料，深色，1440 × 1200。
- `naming.png`：文件重命名面板的局部截图，深色，1121 × 644。截图停留在选择阶段，没有生成或执行改名。
- `accounts.png`：未登录的演示账号状态，深色，1440 × 1040。

截图由 [`tests/browser/browser_readme_screenshots.py`](../../tests/browser/browser_readme_screenshots.py) 从当前生产前端构建生成：脚本用本地静态服务托管 `frontend/dist`，并在浏览器里拦截全部工作台 API，返回固定的演示项目、字幕、章节、学习材料、封面和模型名称。截图上下文固定使用深色配色，未读取个人归档、Cookie 或密钥，未访问平台、登录、下载、调用真实模型或执行磁盘改名。

更新截图时先运行 `npm.cmd --prefix frontend run build`，再运行该脚本；脚本只写入本目录的 PNG。提交前仍需人工检查图片，避免把本机真实课程、账号、路径或凭据发布到仓库。不要将这些界面演示当作真实转写、翻译或下载成功的证明。
