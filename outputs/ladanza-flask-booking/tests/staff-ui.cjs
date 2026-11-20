const {JSDOM}=require('jsdom'),fs=require('fs'),assert=require('assert/strict'),vm=require('vm');
const base=require('path').resolve(__dirname,'..')+'/';
const dom=new JSDOM(fs.readFileSync(base+'admin.html','utf8'),{url:'https://booking.example',runScripts:'outside-only'}),w=dom.window;
const run=code=>new vm.Script(code).runInContext(dom.getInternalVMContext()),get=s=>w.document.querySelector(s),all=s=>[...w.document.querySelectorAll(s)];
w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};
const dates=[1,2,3].map(offset=>{const d=new Date();d.setUTCDate(d.getUTCDate()+offset);return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);});
const flush=async()=>{for(let i=0;i<6;i++)await new Promise(r=>setImmediate(r));};
const event=(selector,name)=>get(selector).dispatchEvent(new w.Event(name,{bubbles:true,cancelable:true}));
let customer={id:'c1'+'1'.repeat(32),revision:'v1',name:'<img src=x onerror=alert(1)>',kana:'てすと',phone:'09012345678',note:'月曜サロン',favorite:true};
let rows=[customer],posted=[],saved=null,saveConflict=false,failList=false,failSlots=false,bookingMode='ok',slotResolver=null;
const ok=data=>({ok:true,status:200,json:async()=>data});
w.fetch=async(url,options={})=>{
 const path=new URL(url,'https://booking.example').pathname,params=new URL(url,'https://booking.example').searchParams;
 if(path==='/api/admin/customers'&&options.method==='POST'){saved=JSON.parse(options.body);customer={...saved,revision:'v1'};rows.push(customer);return ok(customer);}
 if(path.startsWith('/api/admin/customers/')&&options.method==='PUT'){saved=JSON.parse(options.body);if(saveConflict)return {ok:false,status:409,json:async()=>({detail:'別の画面で更新されています'})};customer={...saved,revision:'v2'};return ok(customer);}
 if(path==='/api/admin/customers'){if(failList)throw Error('名簿を取得できません');return ok({customers:rows});}
 if(path.startsWith('/api/admin/customers/'))return ok(customer);
 if(path==='/api/admin/customer-history')return ok({from:'2025-11-20',to:'2027-11-20',total:1,candidates:[{event_id:'1d'+'a'.repeat(40),name:'履歴のお客様',phone:'08012345678',menu:'サロン',starts_at:dates[0]+'T13:00:00+09:00'}]});
 if(path.endsWith('/customer'))return ok({event_id:'1d'+'a'.repeat(40),name:'履歴のお客様',phone:'08012345678',menu:'サロン',instructor:'スタジオ主催',customer_id:customer.id});
 if(path==='/api/admin/booking-slots'){
  if(failSlots)throw Error('空き状況を確認できません');
  if(params.get('date')===dates[1])return new Promise(resolve=>slotResolver=resolve);
  return ok([{starts_at:params.get('date')+'T13:00:00+09:00',available:true,remaining:8,capacity:10,duration_minutes:120}]);
 }
 if(path==='/api/admin/bookings'&&options.method==='POST'){posted.push(JSON.parse(options.body));if(bookingMode==='lost'){bookingMode='ok';throw Error('通信が途切れました');}return ok({id:'1d'+'b'.repeat(40),reservation_number:'LD-12345678',starts_at:posted.at(-1).starts_at,reservation_url:'https://booking.example/reservation.html?token='+'b'.repeat(70)});}
 if(path==='/api/admin/month')return ok({month:params.get('month'),days:[]});
 if(path==='/api/admin/bookings')return ok({date:params.get('date'),events:[],availability:[],updated_at:new Date().toISOString()});
 throw Error('Unexpected API '+url);
};
for(const name of ['reservation-tools.js','admin-settings.js','admin-bookings.js','admin-staff.js'])run(fs.readFileSync(base+name,'utf8'));
(async()=>{
 run("config=normalize(defaultConfig());config.recurrence_rules={};config.revision='original';baseline=clone(config);open()");
 await w.LaDanzaStaff.open();await flush();assert.equal(all('.customer-choice').length,1);assert.equal(all('#customerList img').length,0);assert.doesNotMatch(get('#customerList').textContent,/09012345678/);
 get('.customer-choice').click();await flush();assert.match(get('#selectedCustomer').textContent,/09012345678/);assert.equal(all('#selectedCustomer img').length,0);
 get('#proxyDate').value=dates[0];event('#proxyDate','change');await flush();get('#proxyTime').value=get('#proxyTime').options[1].value;event('#proxyTime','change');event('#proxyForm','submit');
 assert.equal(get('#proxyReview').classList.contains('hidden'),false);get('#confirmProxyBooking').click();await flush();assert.equal(posted.length,0);assert.match(get('#proxyError').textContent,/チェック/);
 get('#proxyConfirmed').checked=true;bookingMode='lost';get('#confirmProxyBooking').click();await flush();assert(get('#backProxyBooking').disabled);assert.match(get('#confirmProxyBooking').textContent,/再確認/);
 const first=posted[0];assert.equal(first.customer_id,customer.id);assert.equal(first.customer_revision,'v1');assert.equal(first.phone,undefined);assert.equal(first.name,undefined);
 get('#newCustomer').click();assert(get('#customerEditor').classList.contains('hidden'),'unknown booking must be resolved before changing customer');
 get('#confirmProxyBooking').click();await flush();assert.deepEqual(posted[1],first);assert.equal(get('#proxyResult').classList.contains('hidden'),false);assert.match(get('#staffReceipt').textContent,/LD-12345678/);assert.equal(w.localStorage.length,0,'staff must not save customer tokens in personal local storage');
 get('#copyStaffLink').click();await flush();assert.match(get('#staffCopyStatus').textContent,/コピー/);assert.match(get('#staffConfirmationLink').value,/reservation.html/);
 get('#nextProxyBooking').click();await flush();await w.LaDanzaStaff.fromBooking('1d'+'a'.repeat(40));await flush();assert(get('#customerEditor').classList.contains('hidden'),'registered next booking skips phone registration');assert.equal(get('#proxyMenu').value,'サロン');
 get('#editCustomer').click();get('#customerPhone').value='０８０-１２３４-５６７８';get('#contactConfirmed').checked=true;saveConflict=true;event('#customerForm','submit');await flush();assert.match(get('#customerError').textContent,/更新/);assert.equal(get('#customerPhone').value,'08012345678');assert.equal(saved.revision,'v1');
 get('#closeCustomerEditor').click();get('#newCustomer').click();get('#customerName').value='新しい方';get('#customerPhone').value='09098765432';get('#contactConfirmed').checked=false;event('#customerForm','submit');await flush();assert.equal(saved.name,customer.name,'confirmation is required before registration');
 get('#contactConfirmed').checked=true;get('#customerFavorite').checked=true;saveConflict=false;event('#customerForm','submit');await flush();assert.equal(saved.name,'新しい方');assert(saved.contact_confirmed);assert.match(get('#selectedCustomer').textContent,/新しい方/);
 get('#proxyDate').value=dates[1];event('#proxyDate','change');await flush();get('#proxyDate').value=dates[2];event('#proxyDate','change');await flush();slotResolver(ok([{starts_at:dates[1]+'T10:00:00+09:00',available:true,capacity:10,remaining:8,duration_minutes:120}]));await flush();assert(get('#proxyTime').options[1].value.startsWith(dates[2]));
 failSlots=true;get('#reloadProxySlots').click();await flush();assert(get('#proxyTime').disabled);assert(get('#reviewProxyBooking').disabled);assert.match(get('#proxySlotStatus').textContent,/確認できません/);
 failList=true;event('#customerSearchForm','submit');await flush();assert.match(get('#customerList').textContent,/取得できません/);assert.equal(all('#customerList .customer-choice').length,0);
 console.log('PASS: name selection, safe rendering, confirmed contact registration, staff-only identity, review, lost-response replay, no personal local storage, copy fallback, next booking, edit conflict, slot request ordering and service errors');w.close();
})().catch(error=>{console.error(error);w.close();process.exitCode=1});
