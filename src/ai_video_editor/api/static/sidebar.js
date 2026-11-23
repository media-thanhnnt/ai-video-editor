/* One shared navigation UI, isolated from all page-level CSS. */
(() => {
  'use strict';
  const routes = [
    ['/', 'Dashboard', '▦'],
    ['/script-editor', 'Script Editor', '✎'],
    ['/scene-planner', 'Scene Planner', '▤'],
    ['/asset-workspace', 'Asset Workspace', '▣'],
    ['/render-studio', 'Render Studio', '▶'],
    ['/api-settings', 'API Settings', '⚙'],
    ['/help', 'Hướng dẫn sử dụng', '❔'],
  ];
  const styles = `
    :host { display:block; box-sizing:border-box; font-family:Inter,system-ui,'Segoe UI',sans-serif; }
    *, *::before, *::after { box-sizing:border-box; }
    .studio-brand { display:flex; align-items:center; gap:10px; padding:8px 12px 22px; margin-bottom:8px; color:#fff; font-weight:750; font-size:17px; line-height:1.4; }
    .studio-logo { font-size:22px; }
    nav { display:flex; flex-direction:column; gap:5px; margin:0; padding:0; }
    nav a { display:flex; align-items:center; gap:10px; padding:12px 13px; border-radius:9px; background:transparent; color:#c4d2e5; text-decoration:none; font-weight:550; font-size:13px; line-height:1.35; white-space:nowrap; }
    nav a:hover { background:#243853; color:#fff; }
    nav a.active, nav a[aria-current=page] { background:#304b75; color:#fff; font-weight:700; }
    .nav-icon { display:inline-block; width:19px; flex:0 0 19px; text-align:center; font-size:17px; }
    .studio-foot { margin-top:34px; padding:12px 13px; color:#99adc6; font-size:11px; line-height:1.7; }
    @media(max-width:760px) {
      .studio-brand { padding:9px 7px; }
      .brand-label, .nav-label, .studio-foot { display:none; }
      nav a { justify-content:center; padding:13px 4px; }
      .nav-icon { width:auto; flex:0 0 auto; }
    }
  `;
  function mount() {
    const aside = document.getElementById('app-sidebar');
    if (!aside) return;
    const shadow = aside.shadowRoot || aside.attachShadow({mode:'open'});
    const sheet = document.createElement('style'); sheet.textContent = styles;
    const brand = document.createElement('div'); brand.className='studio-brand';
    const logo = document.createElement('span'); logo.className='studio-logo'; logo.textContent='🎬';
    const title = document.createElement('span'); title.className='brand-label'; title.textContent='AI Video Editor';
    brand.append(logo,title);
    const nav = document.createElement('nav'); nav.setAttribute('aria-label','Main navigation');
    for (const [url,label,icon] of routes) {
      const a=document.createElement('a'); a.href=url; a.title=label;
      const pictogram=document.createElement('span'); pictogram.className='nav-icon'; pictogram.textContent=icon;
      const text=document.createElement('span'); text.className='nav-label'; text.textContent=label;
      a.append(pictogram,text);
      if (location.pathname.replace(/\/$/,'') === url.replace(/\/$/,'')) {
        a.classList.add('active'); a.setAttribute('aria-current','page');
      }
      nav.append(a);
    }
    const foot=document.createElement('div'); foot.className='studio-foot';
    foot.textContent='Local workspace · Windows / Linux · CLI available';
    shadow.replaceChildren(sheet,brand,nav,foot);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',mount);
  else mount();
})();
