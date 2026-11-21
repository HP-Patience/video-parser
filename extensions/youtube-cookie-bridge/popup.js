// Reads the live session straight from the browser, so HttpOnly cookies such as SID are included.
// An export from the youtube.com tab cannot see them, which is why that route always missed SID.
const TARGET = 'http://127.0.0.1:15001/api/workspace/youtube-auth/cookies';
const SOURCES = [
  'https://www.youtube.com',
  'https://m.youtube.com',
  'https://accounts.google.com',
  'https://www.google.com'
];
const SESSION = ['SID', '__Secure-1PSID', '__Secure-3PSID'];

function netscape(cookies) {
  const rows = ['# Netscape HTTP Cookie File'];
  for (const cookie of cookies) {
    const domain = cookie.domain.startsWith('.') ? cookie.domain : `.${cookie.domain}`;
    const expires = cookie.expirationDate ? Math.floor(cookie.expirationDate) : 0;
    rows.push([
      domain,
      'TRUE',
      cookie.path || '/',
      cookie.secure ? 'TRUE' : 'FALSE',
      String(expires),
      cookie.name,
      cookie.value
    ].join('\t'));
  }
  return `${rows.join('\n')}\n`;
}

async function collect() {
  const seen = new Map();
  for (const url of SOURCES) {
    try {
      for (const cookie of await chrome.cookies.getAll({ url })) {
        seen.set(`${cookie.domain}\t${cookie.path}\t${cookie.name}`, cookie);
      }
    } catch (error) {
      // A missing host permission must not abort the whole collection.
      console.warn('read failed', url, error);
    }
  }
  return [...seen.values()];
}

function show(message, kind) {
  const status = document.getElementById('status');
  status.textContent = message;
  status.className = kind || '';
}

async function send() {
  const button = document.getElementById('send');
  button.disabled = true;
  show('正在读取浏览器 cookie…');
  try {
    const cookies = await collect();
    const names = new Set(cookies.map(cookie => cookie.name));
    const signedIn = SESSION.some(name => names.has(name));
    show(`读到 ${cookies.length} 条 cookie${signedIn ? '' : '（没有 SID，可能未登录 YouTube）'}，正在发送…`);

    const response = await fetch(TARGET, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: netscape(cookies) })
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`${response.status}：${detail.slice(0, 140)}`);
    }
    const result = await response.json();
    show(`已导入 ${result.cookies} 条，登录态${result.complete ? '完整' : '不完整' }。可以关闭此窗口。`, 'ok');
    button.textContent = '再次发送';
  } catch (error) {
    show(`失败：${error.message}。请确认 Video Parser 正在运行（127.0.0.1:15001）。`, 'error');
  } finally {
    button.disabled = false;
  }
}

document.getElementById('send').addEventListener('click', send);
