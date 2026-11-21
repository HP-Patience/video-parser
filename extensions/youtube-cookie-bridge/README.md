# YouTube 登录态桥接扩展

把本机浏览器里的 YouTube 登录态直接发送给本地 Video Parser。

## 为什么需要它

`yt-dlp` 解析和下载需要一份完整的 YouTube 会话。而：

| 方式 | 结果 |
|---|---|
| 从 `youtube.com` 标签页导出 cookies.txt | 缺 `SID` / `__Secure-1PSID` —— 这两个在 `.google.com` 域 |
| 从 Chrome 的 cookie 数据库复制 | Chrome 127+ 的 App-Bound 加密绑定安装路径和 profile 路径，复制出来解不开 |
| 让 Chrome 开远程调试端口（CDP） | Chrome 136+ 禁止在默认 profile 上启用，157 实测端口不监听 |
| **扩展读取（本扩展）** | 由浏览器解密后交给扩展，`SID` 一定在 |

扩展通过 `chrome.cookies.getAll()` 读取，这是浏览器内部接口，能拿到 `HttpOnly` 的会话 cookie。

## 安装

1. Chrome 打开 `chrome://extensions`
2. 右上角打开 **开发者模式**
3. 点 **加载已解压的扩展程序**，选择本目录（`extensions/youtube-cookie-bridge`）
4. 工具栏会出现一个拼图图标，可以把它固定到工具栏

## 使用

1. 确认这个浏览器已登录 YouTube（右上角是你的头像）
2. 点扩展图标 → **发送到 Video Parser**
3. 看到「已导入 N 条，登录态完整」即成功

## 安全

- 请求只发往 `http://127.0.0.1:15001`（本机），没有任何外部地址
- 后端只接受 `chrome-extension://` 来源，其他网页来源一律 403
- 后端只保存 `youtube.com` / `google.com` 域的条目，其余丢弃
- 接口响应只返回条数与完整性，不含 cookie 值

## 依赖

Video Parser 需要在运行中（默认 `http://127.0.0.1:15001`）。如果改了端口，请同步修改 `popup.js` 里的 `TARGET`。
