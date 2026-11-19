// The parser supplies canonical URLs. Only known player origins may be framed.
export function platformPlayerURL(source: string, part?: number | null): string | null {  let url: URL;
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
    case 'www.youtube.com':
      // Deliberately not framed: youtube-nocookie is anonymous by design, and from a 127.0.0.1 page
      // the browser blocks third-party cookies, so the embed can never see the imported login and
      // always shows YouTube's "sign in to confirm you're not a bot" wall. The workspace opens the
      // watch page instead, where the reader's own session applies.
      return null;
    case 'www.douyin.com':
    case 'www.iesdouyin.com': {
      const id = /^\/video\/([0-9]+)\/?$/.exec(url.pathname)?.[1];
      return id ? `https://open.douyin.com/player/video?vid=${id}&autoplay=0` : null;
    }
    default: return null;
  }
}

/** Platforms whose page cannot be framed here, so the workbench points at the original site. */
export function platformOpensExternally(source: string): boolean {
  try {
    return new URL(source).hostname === 'www.youtube.com';
  } catch {
    return false;
  }
}
