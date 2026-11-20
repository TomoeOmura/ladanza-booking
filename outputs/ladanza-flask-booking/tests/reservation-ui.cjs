const {JSDOM}=require('jsdom'),fs=require('fs'),path=require('path'),assert=require('assert/strict'),vm=require('vm');
const base=path.resolve(__dirname,'..'),key='ladanza.reservations.v1';
const tick=()=>new Promise(r=>setImmediate(r));
const token='a'.repeat(90),number='LD-1234ABCD',start='2030-01-07T13:00:00+09:00';
const json=(body,status=200)=>({ok:status<400,status,json:async()=>body});
const booking={reservation_number:number,starts_at:start,menu:'サロン',instructor:'スタジオ主催',status:'confirmed'};
function setup(page,{storage,blocked=false,url='https://booking.example.com/',fetch=async()=>json({})}={}){
 const dom=new JSDOM(fs.readFileSync(path.join(base,page),'utf8'),{url,runScripts:'outside-only'}),w=dom.window;
 w.fetch=fetch;w.confirm=()=>true;w.alert=()=>{};
 if(blocked)Object.defineProperty(w,'localStorage',{get(){throw Error('blocked')}});else if(storage!==undefined)w.localStorage.setItem(key,storage);
 const run=code=>new vm.Script(code).runInContext(dom.getInternalVMContext());
 run(fs.readFileSync(path.join(base,'reservation-tools.js'),'utf8'));
 for(const script of w.document.querySelectorAll('script')){
  if(script.src.endsWith('reservation-tools.js?v=1'))continue;
  if(script.src){const name=new URL(script.src).pathname.slice(1);run(fs.readFileSync(path.join(base,name),'utf8'))}else if(script.textContent.trim())run(script.textContent);
 }
 return {dom,w,$:s=>w.document.querySelector(s)};
}
async function makeBooking(blocked=false){
 let posts=0,payload;
 const env=setup('index.html',{blocked,fetch:async(url,options)=>{
  if(url.startsWith('/api/slots'))return json([{id:'slot-1',starts_at:start,duration_minutes:30,available:true,capacity:10,remaining:5}]);
  assert.equal(url,'/api/bookings');posts++;payload=JSON.parse(options.body);
  return json({id:'event',reservation_number:number,starts_at:start,reservation_url:'https://booking.example.com/reservation.html?token='+token},201);
 }}),{w,$}=env;
 $('[data-menu="サロン"]').click();$('#next').click();await tick();$('[data-date]').click();$('[data-slot]').click();$('#next').click();
 $('#name').value='<img src=x> テスト';$('#phone').value='090a12345679';$('#privacy').checked=true;$('#next').click();await tick();assert.equal($('#stepNo').textContent,'03');assert.equal(posts,0);
 $('#phone').value='０９０（１２３４）５６７９';$('#phone').dispatchEvent(new w.Event('input'));$('#next').click();await tick();
 assert.equal(posts,0,'Review must not create a reservation');assert.equal($('#stepNo').textContent,'04');assert.equal($('#reviewPhone').textContent,'09012345679');assert.equal($('#reviewName img'),null,'Review must render untrusted names as text');
 $('#back').click();assert.equal($('#name').value,'<img src=x> テスト');$('#next').click();await tick();$('#next').click();await tick();
 assert.equal(posts,1);assert.equal(payload.phone,'09012345679');assert(!$('#success').classList.contains('hidden'));assert.equal($('#cancelLink').href,'https://booking.example.com/reservation.html?token='+token);
 return env;
}
(async()=>{
 const created=await makeBooking();const saved=created.w.localStorage.getItem(key);assert.equal(JSON.parse(saved).length,1);assert(!saved.includes('09012345679'));assert(!saved.includes('テスト'));assert.match(created.$('#savedNotice').textContent,/保存しました/);created.w.close();
 // Saved links work after reopening, independent of the mistyped phone; display live data.
 let requests=[];let env=setup('reservation-lookup.html',{storage:saved,fetch:async(url,options)=>{requests.push({url,options});return json({...booking,starts_at:'2030-01-08T19:00:00+09:00',status:'cancelled'})}});await tick();
 assert.equal(env.$('#savedBookings').children.length,1);assert.match(env.$('#savedBookings').textContent,/キャンセル済み/);assert.match(env.$('#savedBookings').textContent,/1月8日/);assert.equal(requests.length,1);assert.equal(requests[0].options.cache,'no-store');assert.equal(env.$('#savedBookings a').getAttribute('href'),'/reservation.html?token='+token);
 env.$('.forget').click();await tick();assert.equal(JSON.parse(env.w.localStorage.getItem(key)).length,0);assert.equal(requests.length,1,'Removing from this browser must not cancel');env.w.close();
 env=setup('reservation-lookup.html',{storage:saved,fetch:async()=>{throw Error('offline')}});await tick();assert.match(env.$('#savedBookings').textContent,/予約の有無は判断できません/);assert.equal(JSON.parse(env.w.localStorage.getItem(key)).length,1);env.w.close();
 env=setup('reservation-lookup.html',{storage:'broken-json'});await tick();assert.match(env.$('#savedMessage').textContent,/読み込めません/);assert.equal(env.w.localStorage.getItem(key),'broken-json');env.w.close();
 const failed=await makeBooking(true);assert.match(failed.$('#savedNotice').textContent,/予約は完了していますが/);assert.match(failed.$('#savedNotice').textContent,/保存できませんでした/);failed.$('#copyLink').click();await tick();assert.equal(failed.$('#linkField').hidden,false);assert.match(failed.$('#linkField').value,/token=/);failed.w.close();
 env=setup('reservation.html',{url:'https://booking.example.com/reservation.html?token='+token,fetch:async()=>json(booking)});await tick();assert.equal(JSON.parse(env.w.localStorage.getItem(key)).length,1);env.w.LaDanzaReservations.remember({token:'b'.repeat(90),reservation_number:number,starts_at:start});assert.equal(JSON.parse(env.w.localStorage.getItem(key)).length,1,'Reissued links replace same reservation in list');env.w.close();
 // Empty/no-match lookup has a direct recovery path, and fullwidth numbers are normalized.
 let searched;
 env=setup('reservation-lookup.html',{fetch:async(url,options)=>{searched=JSON.parse(options.body);return json({},404)}});await tick();env.$('#contact').value='０９０－１２３４－５６７８';await env.$('#form').onsubmit({preventDefault(){},currentTarget:env.$('#form')});assert.equal(searched.contact,'09012345678');assert.equal(env.$('#help').open,true);assert.match(env.$('#message').textContent,/番号を間違えて/);env.w.close();
 // Staff contact repair is deliberate and returns a usable link without sending a message.
 let recoverySaved;
 env=setup('admin.html',{fetch:async(url,options)=>{
  if(options?.method==='POST'){recoverySaved=JSON.parse(options.body);return json({reservation_url:'https://booking.example.com/reservation.html?token='+token,revision:'next'})}
  return json({...booking,customer_name:'確認するお客様',phone:'09012345679',revision:'current'});
 }});
 const content=env.w.document.createElement('div'),button=env.w.document.createElement('button');env.w.document.body.append(content,button);
 await env.w.openBookingRecovery({event_id:'test-booking'},content,button);
 const form=content.querySelector('form'),phone=form.querySelector('input[type=tel]'),identity=form.querySelector('input[type=checkbox]');phone.value='０９０１２３４５６７８';await form.onsubmit({preventDefault(){}});assert.equal(recoverySaved,undefined);identity.checked=true;await form.onsubmit({preventDefault(){}});assert.equal(recoverySaved.phone,'09012345678');assert.equal(recoverySaved.revision,'current');assert.equal(recoverySaved.identity_confirmed,true);assert.match(content.textContent,/確認リンクを発行しました/);env.w.close();
 console.log('PASS: booking review, typo-independent saved confirmation, persistence, current cancellation/time, no contact storage, safe text, removal without cancellation, offline/invalid storage, storage failure with link backup, reissued-link deduplication, fullwidth search, no-match help and verified staff recovery');
})().catch(error=>{console.error(error);process.exitCode=1});
