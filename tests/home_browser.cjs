// Run with node tests/home_browser.cjs; set PLAYWRIGHT_MODULE to a local Playwright installation.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const url=process.env.HOME_TEST_URL||'http://127.0.0.1:8766/home.html';
const output=process.env.HOME_SCREENSHOTS||path.join(process.env.TEMP||'/tmp','closebrief-home-qa');
(async()=>{
 fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  for(const colorScheme of ['light','dark'])for(const width of [1440,1024,375]){
   const context=await browser.newContext({viewport:{width,height:900},colorScheme,isMobile:width===375,hasTouch:width===375});
   const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(String(e)));
   await page.goto(url);await page.evaluate(()=>document.fonts.ready);
   const layout=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth,bottom:document.querySelector('.ws').getBoundingClientRect().bottom,bg:getComputedStyle(document.body).backgroundColor}));
   assert(layout.scroll<=width,`Horizontal overflow ${width} ${colorScheme}: ${JSON.stringify(layout)}`);
   if(width===1440)assert(layout.bottom<=900,`Preview below fold: ${layout.bottom}`);
   assert.equal(await page.locator('.checklist li').count(),5);
   assert.equal(await page.locator('.glance div').count(),4);
   assert.equal(await page.locator('.flow li').count(),5);
   assert.equal(await page.locator('[role=progressbar]').getAttribute('aria-valuenow'),'64');
   assert.equal(await page.locator('[data-cta]').first().getAttribute('href'),'/app?demo=1');
   await page.keyboard.press('Tab');assert(await page.locator('.skip').evaluate(e=>e===document.activeElement));
   assert.equal(await page.locator('.skip').evaluate(e=>getComputedStyle(e).outlineStyle),'solid');
   if(width===375){
    const small=await page.locator('a:visible').evaluateAll(es=>es.filter(e=>e.getBoundingClientRect().height<40).map(e=>({text:e.textContent.trim(),height:e.getBoundingClientRect().height})));
    assert.deepEqual(small,[],'Touch targets below 40px');
   }
   assert.deepEqual(errors,[]);
   await page.locator('#hero-title').click();
   await page.screenshot({path:path.join(output,`home-${width}-${colorScheme}.png`),fullPage:width===375});
   await page.locator('.ws').screenshot({path:path.join(output,`workspace-${width}-${colorScheme}.png`)});
   console.log('PASS',width,colorScheme,JSON.stringify(layout));await context.close();
  }
  const cases=[
   {name:'newest unfinished close',data:[{period:'2026-08',status:'shared'},{period:'2026-05',status:'in_progress'},{period:'2026-06',status:'in_progress'}],want:'Continue June close'},
   {name:'HTTP error',status:401,want:'Open your workspace'},
   {name:'malformed response',data:{months:[]},want:'Open your workspace'},
   {name:'no unfinished close',data:[{period:'2026-06',status:'shared'}],want:'Open your workspace'}
  ];
  for(const test of cases)for(const width of [1440,1024,375]){
   const context=await browser.newContext({viewport:{width,height:900}});await context.addCookies([{name:'cb_signed_in',value:'1',url}]);await context.addInitScript(()=>{localStorage.setItem('cb-ws','7');});
   const page=await context.newPage();
   await page.route('**/close/months',route=>{assert.equal(route.request().headers().authorization,undefined);assert.equal(route.request().headers()['x-closebrief-client'],'1');assert.equal(route.request().headers()['x-workspace-id'],'7');return route.fulfill({status:test.status||200,contentType:'application/json',body:JSON.stringify(test.data||{})});});
   const response=page.waitForResponse('**/close/months');
   await page.goto(url);await response;await page.waitForFunction(want=>document.querySelector('[data-cta-label]').textContent===want,test.want);
   assert.equal(await page.locator('#nav-signin').textContent(),test.want);
   assert.equal(await page.locator('[data-cta]').first().getAttribute('href'),test.want.startsWith('Continue')?'/app#month=2026-06&tab=overview':'/app');
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Returning user horizontal overflow');
   console.log('PASS CTA',test.name,width);await context.close();
  }
 }finally{await browser.close();}
 console.log('Screenshots:',output);
})().catch(e=>{console.error(e);process.exit(1);});
