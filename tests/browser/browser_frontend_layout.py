"""Layout regression on the real React build. All workspace APIs are mocked.
Traditional, hidden/overlay-style and old-browser scrollbar fallback are exercised.
No real parse/download/upload/login/inference/cache operation is allowed.
"""
import base64,json,os
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect
# Button-surface checks were dropped on 2026-10-14: the approved minimal design system uses borderless ghost buttons.
ROOT=Path(__file__).resolve().parents[2]
if (ROOT/'runtimes/browsers').is_dir():os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'runtimes/browsers'))
OUT=ROOT/'.local/work/artifacts/frontend-layout-verification'
URL=os.environ.get('FRONTEND_LAYOUT_URL','http://127.0.0.1:15001')
WIDTHS=[1920,1551,1550,1549,1440,1171,1170,1169,1024,1001,1000,999,768,761,760,390,350,320,280]
PAGES=[('workspace','视频工作台'),('transcription','字幕与转写'),('tasks','任务中心'),('accounts','账号与登录'),('settings','设置')]
TABS=['视频','AI 分析']  # 2026-10-14: B站字幕 tab merged into 字幕与转写

def main():
 OUT.mkdir(parents=True,exist_ok=True);checks=[];measurements=[];errors=[];unexpected=[];posts=[]
 image='data:image/svg+xml;base64,'+base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450"><rect width="800" height="450" fill="#1c392a"/><text x="285" y="220" fill="white">LAYOUT FIXTURE</text></svg>').decode()
 project={'id':'a'*32,'title':'布局验证素材','platform':'哔哩哔哩','part':1,'location':'mock/layout','identity_status':'verified','archive_url':'/mock/archive','resources':[]}
 video={'id':'1'*32,'title':project['title'],'platform':'哔哩哔哩','source_url':'https://www.bilibili.com/video/LAYOUTFIXTURE','cover_url':image,'cover_download_url':image,'warning':None,'project':project,'media':None,'qualities':[{'id':'1080','label':'1080P','height':1080,'width':1920,'media':None}],'default_quality_id':'1080','analysis':''}
 jobs=[{'id':f'{i+100:032x}','kind':'video','state':'completed','phase':'completed','title':f'布局模拟任务 {i}','message':'已完成'} for i in range(55)]
 accounts={'storage':'.local/credentials','platforms':[{'id':k,'name':n,'benefit':'布局验证','qr_login':True,'qr_mode':'bili' if k=='bilibili' else 'browser','manual_finish':k=='xiaohongshu','state':'missing','saved_at':None,'expires_at':None,'login':None} for k,n in [('bilibili','哔哩哔哩'),('douyin','抖音'),('xiaohongshu','小红书')]]}
 config={'platforms':['哔哩哔哩'],'ai_configured':True,'media_ready':True,'subtitles_ready':True,'transcription_ready':True,'archive_root':'D:/mock/library'}
 def check(label,condition=True):
  assert condition,label;checks.append(label)
 def close(a,b):return abs(a-b)<=0.5
 with sync_playwright() as pw:
  from unittest.mock import patch as _patch;from video_parser.services import ai_settings
  with _patch.object(ai_settings,'SETTINGS_PATH',OUT/'no-ai-settings.json'),_patch.dict(os.environ,{'QWEN_API_KEY':''}):AI=ai_settings.public()
  for mode in ['classic','hidden','fallback']:
   browser=pw.chromium.launch(headless=True,**({'ignore_default_args':['--hide-scrollbars']} if mode!='hidden' else {}))
   context=browser.new_context(viewport={'width':1920,'height':900});page=context.new_page();page.set_default_timeout(10000);page.on('pageerror',lambda e:errors.append(str(e)))
   def route_api(route):
    req=route.request;path=urlsplit(req.url).path.split('/api/workspace',1)[-1]
    if req.method=='POST':posts.append(path)
    if path=='/config':route.fulfill(json=config)
    elif path=='/tasks':route.fulfill(json={'jobs':jobs})
    elif path=='/accounts':route.fulfill(json=accounts)
    elif path=='/transcriptions/sources':route.fulfill(json={'sources':[]})
    elif path=='/projects':route.fulfill(json={'projects':[]})
    elif path=='/folders':route.fulfill(json={'folders':[]})
    elif path=='/ai/settings':route.fulfill(json=AI)
    elif path.endswith('/analysis/context'):route.fulfill(json={'transcripts':[],'duration':120,'auto_frame_count':8,'image_tokens':800,'prompt_tokens':900,'output_tokens':1500,'templates':[{'id':'summary','name':'内容摘要','description':'要点概括','source':'both','requires':None}],'sources':{'both':'字幕 + 画面','transcript':'只用字幕','frames':'只用画面'},'ai':AI})
    elif path.endswith('/analyses'):route.fulfill(json={'records':[]})
    elif path=='/parse' or path.startswith('/projects/') and path.endswith('/resume'):route.fulfill(json=video)
    else:unexpected.append(path);route.fulfill(status=400,json={'detail':'Layout test blocks all processing operations'})
   context.route('**/api/workspace/**',route_api)
   def settle():page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
   def nav(name):
    if page.locator('.mobile-menu').is_visible() and not page.locator('.app-sidebar').evaluate('(e)=>e.classList.contains("open")'):page.locator('.mobile-menu').click()
    if name=='字幕与转写':
      page.locator('.app-sidebar').get_by_role('button',name='视频工作台',exact=True).click();settle()
      page.get_by_role('tab',name='字幕与转写',exact=True).click();settle();return
    page.locator('.app-sidebar').get_by_role('button',name=name,exact=True).click();settle()
   def metrics(key):
    return page.evaluate('''key => {const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
      const section=document.querySelector(`[data-testid="page-${key}"]`), main=document.querySelector('.modern-main'), title=document.querySelector('.topbar-title');
      return {main:box(main),title:box(title),content:box(section),titleText:title.textContent,titleId:title.id,titleFont:parseFloat(getComputedStyle(title).fontSize),titleFits:title.scrollWidth<=title.clientWidth,actions:box(document.querySelector('.top-actions')),sidebar:box(document.querySelector('.app-sidebar')),topbar:box(document.querySelector('.app-topbar')),client:document.documentElement.clientWidth,usable:document.body.getBoundingClientRect().width,scrollWidth:document.documentElement.scrollWidth,scrollY,scrolling:document.documentElement.scrollHeight>innerHeight};}''',key)
   try:
    page.goto(URL,wait_until='networkidle');expect(page.locator('.task-list>li')).to_have_count(55)
    if mode=='fallback':page.add_style_tag(content='html { scrollbar-gutter:auto !important; overflow-y:scroll !important; }')
    nav('字幕与转写');page.locator('#transcription-file').set_input_files({'name':'layout-draft.wav','mimeType':'audio/wav','buffer':b'mock-layout-draft'})
    nav('视频工作台');page.locator('#source').fill(video['source_url']);page.locator('.parse-button').click();expect(page.locator('.info-title')).to_have_text(video['title']);settle()
    for width in WIDTHS:
     page.set_viewport_size({'width':width,'height':900});settle();snapshots=[]
     for key,name in PAGES:
      nav(name);m=metrics(key);snapshots.append(m);measurements.append({'mode':mode,'width':width,'page':key,**m})
      check(f'{mode} {width} {key}: no overflow in actual client area',m['scrollWidth']<=m['client'])
      check(f'{mode} {width} {key}: single main heading lives in topbar',page.locator('h1').count()==1 and page.locator('.app-topbar h1').count()==1 and page.locator('.breadcrumb,.page-heading,.workbench-page h1').count()==0)
      check(f'{mode} {width} {key}: topbar title names current page',m['titleText']==name and m['titleId']=='page-title-'+key)
      check(f'{mode} {width} {key}: title remains large and untruncated',m['titleFont']>=21 and m['titleFits'])
      check(f'{mode} {width} {key}: title and actions fit inside topbar',m['title']['x']+m['title']['width']<=m['actions']['x']+0.5 and m['title']['y']>=m['topbar']['y'] and m['title']['y']+m['title']['height']<=m['topbar']['y']+m['topbar']['height']+0.5)
      check(f'{mode} {width} {key}: no empty content heading space',0<=m['content']['y']-m['topbar']['y']-m['topbar']['height']<=29.5)
      check(f'{mode} {width} {key}: visible page and main use existing title',page.locator('.workbench-page:visible').get_attribute('aria-labelledby')==m['titleId'] and page.locator('.modern-main').get_attribute('aria-labelledby')==m['titleId'])
      if width<=760:check(f'{mode} {width} {key}: mobile navigation focuses topbar heading',page.locator('.topbar-title').evaluate('(e)=>e===document.activeElement'))
      check(f'{mode} {width} {key}: navigation starts at top',m['scrollY']==0)
      check(f'{mode} {width} {key}: one visible page',page.locator('.workbench-page:visible').count()==1)
      if key=='transcription':
       outer=page.locator('.transcription-layout').bounding_box();inner=page.locator('.transcription-panel').bounding_box()
       check(f'{mode} {width}: audio has no empty responsive column',close(outer['width'],inner['width']) and close(outer['x'],inner['x']))
       check(f'{mode} {width}: selected file survives page switches','layout-draft.wav' in page.locator('.transcription-panel').inner_text())
       check(f'{mode} {width}: language is auto-detected (no language select)',page.locator('#transcription-language').count()==0)
      if key=='accounts':
       check(f'{mode} {width}: three platforms listed',page.locator('.account-list>li').count()==3)
       check(f'{mode} {width}: all task rows remain accessible',page.locator('.task-list>li').count()==55)
       page.evaluate('window.scrollTo(0,700)');check(f'{mode} {width}: long-page scroll scenario exercised (task list scrolls inside its panel when the page fits)',page.evaluate('scrollY>0 || document.documentElement.scrollHeight<=innerHeight+1'));expect(page.locator('.topbar-title')).to_be_in_viewport();check(f'{mode} {width}: main title remains visible while scrolling');nav('字幕与转写');check(f'{mode} {width}: leaving scrolled task page resets position',page.evaluate('scrollY==0'))
     baseline=snapshots[0]
     for m in snapshots[1:]:
      check(f'{mode} {width}: page main boundary does not drift',close(baseline['main']['x'],m['main']['x']) and close(baseline['main']['width'],m['main']['width']))
      check(f'{mode} {width}: page title anchor does not drift',close(baseline['title']['x'],m['title']['x']) and close(baseline['title']['y'],m['title']['y']))
      check(f'{mode} {width}: scrollbar allocation does not change',close(baseline['usable'],m['usable']))
     nav('视频工作台');tab_snapshots=[]
     for tab in TABS:
      page.get_by_role('tab',name=tab,exact=True).click();settle();r=page.locator('.tabs').bounding_box();tab_snapshots.append(r)
      check(f'{mode} {width} {tab}: workspace remains within client area',page.evaluate('document.documentElement.scrollWidth<=document.documentElement.clientWidth'))
     first=tab_snapshots[0]
     check(f'{mode} {width}: all tabs share fixed strip anchor',all(close(first['x'],r['x']) and close(first['y'],r['y']) and close(first['width'],r['width']) for r in tab_snapshots))
     if mode=='classic' and width in [1440,390]:
      for _,name in PAGES:
       nav(name);page.screenshot(path=str(OUT/f'{name}-{width}.png'),full_page=True)
      nav('视频工作台');page.get_by_role('tab',name='视频',exact=True).click();page.screenshot(path=str(OUT/f'封面图片-{width}.png'),full_page=True)
     if mode=='classic' and width==1920:check('Classic scrollbar gutter is truly allocated',page.evaluate('innerWidth-document.body.getBoundingClientRect().width>0'))
    page.set_viewport_size({'width':1440,'height':900});settle();nav('字幕与转写');page.get_by_role('button',name='设置',exact=True).click();before=page.locator('.modern-main').bounding_box();page.keyboard.press('Escape');settle();after=page.locator('.modern-main').bounding_box();check(f'{mode}: closing settings leaves shell geometry unchanged',close(before['x'],after['x']) and close(before['width'],after['width']))
    nav('视频工作台');page.get_by_role('tab',name='视频',exact=True).click();page.reload(wait_until='networkidle');expect(page.locator('.info-title')).to_have_text(video['title']);check(f'{mode}: refresh restores project with no processing replay',all(path=='/parse' for path in posts))
    check(f'{mode}: no browser runtime exceptions',not errors);check(f'{mode}: every workspace API mocked',not unexpected)
   except Exception:
    page.screenshot(path=str(OUT/f'failure-{mode}.png'),full_page=True);raise
   finally:
    (OUT/'results.json').write_text(json.dumps({'checks':checks,'measurements':measurements,'errors':errors,'unexpected':unexpected,'posts':posts},ensure_ascii=False,indent=2),encoding='utf-8');context.unroute_all(behavior='ignoreErrors');browser.close()
 print(f'PASS: {len(checks)} layout checks')

if __name__=='__main__':main()
