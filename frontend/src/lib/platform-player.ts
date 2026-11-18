// The parser supplies canonical URLs. Only known player origins may be framed.
export function platformPlayerURL(source: string, part?: number | null): string | null {
  let url: URL;
  try { url = new URL(source); } catch { return null; }
  if (url.protocol !== 'https:' || url.username || url.password || url.port) return null;

  switch (url.hostname) {
    case 'www.bilibili.com': {
      const id = /^\/video\/(BV[A-Za-z0-9]{10}|av[0-9]+)\/?$/.exec(url.pathname)?.[1];
      if (!id) return null;
      const page = part ?? Number(url.searchParams.get('p') || 1);
      const player = new URL('https://player.bilibili.com/player.html');
      player.searchParams.set(id.startsWith('BV') ? 'bvid' : 'aid', id.startsWith('BV') ? id : id.slice(2));
      player.searchParams.set('p', String(Number.isSafeInteger(page) && page > 0 ? page : 1));
      player.searchParams.set('autoplay', '0');
      return player.href;
    }
    case 'www.youtube.com': {
      const id = url.searchParams.get('v');
      return url.pathname === '/watch' && id && /^[A-Za-z0-9_-]{11}$/.test(id)
        ? `https://www.youtube-nocookie.com/embed/${id}` : null;
    }
    case 'www.douyin.com':
    case 'www.iesdouyin.com': {
      const id = /^\/video\/([0-9]+)\/?$/.exec(url.pathname)?.[1];
      return id ? `https://open.douyin.com/player/video?vid=${id}&autoplay=0` : null;
    }
    default: return null;
  }
}
