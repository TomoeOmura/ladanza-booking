const {JSDOM}=require('jsdom'),fs=require('fs'),assert=require('assert/strict'),vm=require('vm');
const base=require('path').resolve(__dirname,'..')+'/';
const dom=new JSDOM(fs.readFileSync(base+'admin.html','utf8'),{url:'https://booking.example',runScripts:'outside-only'}),w=dom.window;
const run=code=>new vm.Script(code).runInContext(dom.getInternalVMContext()),get=s=>w.document.querySelector(s),all=s=>[...w.document.querySelectorAll(s)];
w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};
let fail=false,saved=null;
w.fetch=async(url,options)=>{
 if(options?.method==='PUT'){if(fail)return {ok:false,status:409,json:async()=>({detail:'別の画面で設定が更新されています'})};saved=JSON.parse(options.body);return {ok:true,json:async()=>({...saved,revision:'new-revision'})}}
 const params=new URL(url,'https://booking.example').searchParams;
 if(fail)return {ok:false,status:503,json:async()=>({detail:'取得できませんでした'})};
 if(params.has('month')){const month=params.get('month');return {ok:true,json:async()=>({month,days:Array.from({length:30},(_,i)=>({date:month+'-'+String(i+1).padStart(2,'0'),counts:{'廣瀬 裕貴':{bookings:i===5?2:0,calendar:0,cancelled:0}},availability:{'廣瀬 裕貴':i===5?'available':'closed'}}))})}}
 const date=params.get('date');return {ok:true,json:async()=>({date,updated_at:date+'T12:00:00+09:00',events:date.endsWith('-06')?[
 {kind:'booking',customer_name:'<img src=x onerror=alert(1)>',resource:'廣瀬 裕貴',instructor:'廣瀬 裕貴',menu:'個人レッスン 30分',starts_at:date+'T10:00:00+09:00',ends_at:date+'T10:30:00+09:00',status:'confirmed'},
 {kind:'booking',customer_name:'キャンセル例',resource:'チャーター',instructor:'スタジオ主催',menu:'チャーター',starts_at:date+'T20:00:00+09:00',ends_at:date+'T20:30:00+09:00',status:'cancelled'}]:[],availability:[{resource:'廣瀬 裕貴',status:'available',group:false,slots:[{start:'10:30',end:'11:00'},{start:'17:00',end:'17:30'},{start:'17:30',end:'18:00'}]}]})};
};
run(fs.readFileSync(base+'admin-settings.js','utf8'));run(fs.readFileSync(base+'admin-bookings.js','utf8'));
const change=(selector,value)=>{get(selector).value=value;get(selector).dispatchEvent(new w.Event('change',{bubbles:true}))};
(async()=>{
 run(`config=normalize(defaultConfig());config.recurrence_rules={};config.revision='original';config.date_overrides['廣瀬 裕貴']={'2026-11-01':[...times],'2026-11-05':['18:00','18:30','19:00','19:30'],'2026-11-06':['10:00','10:30',...times.filter(t=>t>='17:00')],'2026-11-16':times.filter(t=>t>='17:00')};baseline=clone(config);open()`);
 w.selectInstructor('廣瀬 裕貴');w.changeDate('2026-11-06');assert.equal(all('.range-row').length,2);assert.equal(all('.range-start')[0].value,'10:00');assert.equal(all('.range-end')[0].value,'11:00');assert.equal(all('.range-end')[1].value,'22:00');assert.equal(run('changes().length'),0);
 w.switchMode('weekly');w.switchMode('date');assert.equal(run('changes().length'),0,'viewing must not dirty saved data');
 change('.range-end','10:30');assert.equal(run('changes().length'),1);assert.match(get('#saveState').textContent,/未保存/);
 w.reviewChanges({preventDefault(){}});assert(get('#saveDialog').open);assert.match(get('#changeSummary').textContent,/廣瀬 裕貴・2026-11-06/);assert.match(get('#changeSummary').textContent,/変更前：10:00〜11:00/);assert.match(get('#changeSummary').textContent,/変更後：10:00〜10:30/);
 fail=true;await w.saveSettings();assert(get('#saveDialog').open);assert.equal(run('changes().length'),1);assert.match(get('#saveError').textContent,/別の画面/);
 fail=false;await w.saveSettings();assert.equal(run('changes().length'),0);assert.equal(saved.date_overrides['廣瀬 裕貴']['2026-11-01'].length,24);assert.equal(saved.date_overrides['廣瀬 裕貴']['2026-11-16'].length,10);assert.equal(saved.revision,'original');
 change('.range-end','10:00');assert.match(get('#editorError').textContent,/開始より後/);assert(get('#reviewSave').disabled);w.selectInstructor('サロン');assert.match(get('#selectedName').textContent,/廣瀬/);change('.range-end','11:00');
 w.switchMode('weekly');change('.repeat-select','1,3');w.reviewChanges({preventDefault(){}});assert.match(get('#changeSummary').textContent,/毎月 第1・第3/);get('#closeSaveDialog').click();
 w.selectInstructor('チャーター');assert.equal(all('.time-block').length,1);assert.equal(get('.repeat-select').value,'1');assert(get('.repeat-select').disabled);w.switchMode('date');w.changeDate('2026-11-12');assert.equal(all('.range-row').length,0);assert(get('#dayOffBtn').disabled);w.changeDate('2026-11-05');assert.equal(all('.time-block').length,1);
 w.selectInstructor('社会人サークル');w.changeDate('2026-11-11');assert.equal(get('.range-start').value,'19:30');assert.equal(get('output.range-end').textContent,'20:30');w.switchMode('weekly');assert.equal(all('.time-block').length,1);assert.equal(get('.repeat-select').value,'2,4');
 w.selectBookingDate('2026-11-06');await w.loadDailyBookings();await w.loadMonthBookings();assert.equal(get('#dailyBookingCount').textContent,'1');assert.equal(all('.daily-card').length,1);assert.equal(all('#bookingList img').length,0);assert.match(get('#bookingList').textContent,/<img/);assert.match(get('#availabilityList').textContent,/17:00〜18:00/);assert.equal(all('.month-day').length,30);assert.match(all('.month-day')[5].textContent,/予約 2件/);
 get('#showCancelledBookings').checked=true;w.renderDailyBookings();assert.equal(all('.daily-card').length,2);
 change('#bookingInstructor','廣瀬 裕貴');assert.equal(all('.daily-card').length,1);get('#editSelectedDay').click();assert.equal(get('#specificDate').value,'2026-11-06');assert.match(get('#selectedName').textContent,/廣瀬/);
 fail=true;await w.loadMonthBookings();await w.loadDailyBookings();assert.equal(all('.daily-card').length,0);assert.match(get('#monthMessage').textContent,/取得できません/);assert.match(all('.month-day')[5].textContent,/未確認/);assert.equal(all('.availability-card').length,0);
 let resolveOld;w.fetch=url=>{const date=new URL(url,'https://booking.example').searchParams.get('date');if(date==='2026-11-01')return new Promise(r=>resolveOld=r);return Promise.resolve({ok:true,json:async()=>({date,updated_at:date+'T12:00:00+09:00',events:[],availability:[]})})};get('#bookingDate').value='2026-11-01';const old=w.loadDailyBookings();get('#bookingDate').value='2026-11-02';await w.loadDailyBookings();resolveOld({ok:true,json:async()=>({date:'2026-11-01',events:[],availability:[]})});await old;assert.match(get('#bookingDayTitle').textContent,/11月2日/);
 console.log('PASS: range round-trip without data loss, non-dirty navigation, save preview/success/conflict, validation, recurring rules, charter restriction, circle duration, monthly counts, safe rendering, free ranges, filters, errors and request ordering');w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
