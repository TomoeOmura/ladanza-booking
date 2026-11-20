let dailyOverview = null;
let dailyRequestNumber = 0;
let dailyInitialized = false;

function japanToday(){
  return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
}

let monthOverview=null,monthRequestNumber=0,calendarMonth='';
function switchAdminView(view){
  if(view==='bookings'&&!commitVisible())return;
  const showingBookings=view==='bookings';
  $('#bookingOverview').classList.toggle('hidden',!showingBookings);
  $('#scheduleEditor').classList.toggle('hidden',showingBookings);
  $('#bookingsTab').setAttribute('aria-pressed',String(showingBookings));
  $('#scheduleTab').setAttribute('aria-pressed',String(!showingBookings));
  if(showingBookings){$('#unsavedNotice').classList.toggle('hidden',!changes().length);loadDailyBookings();loadMonthBookings()}
}
function initializeDailyOverview(){
  if(dailyInitialized)return;dailyInitialized=true;
  $('#bookingDate').value=japanToday();calendarMonth=japanToday().slice(0,7);
  for(const name of instructors){const option=node('option',name);option.value=name;$('#bookingInstructor').append(option)}
  $('#bookingsTab').onclick=()=>switchAdminView('bookings');$('#scheduleTab').onclick=()=>switchAdminView('settings');
  $('#bookingDate').onchange=()=>selectBookingDate($('#bookingDate').value);
  $('#bookingRefresh').onclick=()=>{loadDailyBookings();loadMonthBookings()};
  $('#bookingToday').onclick=()=>selectBookingDate(japanToday());
  for(const [id,offset] of [['bookingPrevious',-1],['bookingNext',1]])$('#'+id).onclick=()=>{const date=new Date(($('#bookingDate').value||japanToday())+'T00:00:00Z');date.setUTCDate(date.getUTCDate()+offset);selectBookingDate(date.toISOString().slice(0,10))};
  for(const [id,offset] of [['monthPrevious',-1],['monthNext',1]])$('#'+id).onclick=()=>{const date=new Date(calendarMonth+'-01T00:00:00Z');date.setUTCMonth(date.getUTCMonth()+offset);selectBookingDate(date.toISOString().slice(0,10))};
  $('#bookingInstructor').onchange=()=>{renderDailyBookings();renderMonth()};$('#showCancelledBookings').onchange=renderDailyBookings;
  $('#editSelectedDay').onclick=()=>{if(!$('#bookingDate').value||!commitVisible())return;selectedDate=$('#bookingDate').value;activeInstructor=$('#bookingInstructor').value||activeInstructor;viewMode='date';renderTabs();renderSchedule();switchAdminView('settings')};
}
function selectBookingDate(value){if(!value)return;$('#bookingDate').value=value;const next=value.slice(0,7);if(calendarMonth!==next){calendarMonth=next;loadMonthBookings()}else renderMonth();loadDailyBookings()}
async function loadMonthBookings(){
  const number=++monthRequestNumber,value=calendarMonth;monthOverview=null;renderMonth();$('#monthMessage').textContent='月の予約状況を確認しています…';$('#monthMessage').classList.remove('error-text');
  try{const response=await fetch('/api/admin/month?'+new URLSearchParams({month:value}),{headers:headers(),cache:'no-store'}),data=await response.json();if(number!==monthRequestNumber)return;if(!response.ok)throw new Error(response.status===401?'ログインし直してからご確認ください。':data.detail||'月の予約状況を取得できませんでした');if(data.month!==value||!Array.isArray(data.days))throw new Error('月の予約状況を確認できませんでした。');monthOverview=data;$('#monthMessage').textContent='';renderMonth()}catch(e){if(number!==monthRequestNumber)return;$('#monthMessage').textContent=e.message;$('#monthMessage').classList.add('error-text')}
}
function renderMonth(){
  if(!calendarMonth)return;const [year,month]=calendarMonth.split('-').map(Number);$('#monthTitle').textContent=year+'年 '+month+'月';$('#monthGrid').replaceChildren();const offset=(new Date(year,month-1,1).getDay()+6)%7,last=new Date(year,month,0).getDate(),resource=$('#bookingInstructor').value;
  for(let i=0;i<offset;i++)$('#monthGrid').append(node('div',undefined,'month-blank'));
  for(let d=1;d<=last;d++){const value=calendarMonth+'-'+String(d).padStart(2,'0'),button=node('button',undefined,'month-day');button.type='button';button.setAttribute('aria-pressed',String(value===$('#bookingDate').value));if(value===japanToday())button.classList.add('is-today');button.append(node('span',String(d),'day-number'));const data=monthOverview?.days.find(day=>day.date===value);let label=value;
    if(data){const counts=Object.entries(data.counts).filter(([key])=>!resource||key===resource).map(([,c])=>c),bookings=counts.reduce((s,c)=>s+c.bookings,0),calendar=counts.reduce((s,c)=>s+c.calendar,0),states=Object.entries(data.availability).filter(([key])=>!resource||key===resource).map(([,s])=>s);const status=states.includes('available')?'空きあり':states.every(s=>s==='closed')?'受付枠なし':value<japanToday()?'受付終了':'空きなし';button.append(node('span','予約 '+bookings+'件','month-count'));if(calendar)button.append(node('span','予定 '+calendar+'件','month-event'));button.append(node('span',status,'month-status '+(status==='空きあり'?'available':'')));label+='、予約'+bookings+'件、予定'+calendar+'件、'+status}
    else button.append(node('span','未確認','month-status'));
    button.setAttribute('aria-label',label);button.onclick=()=>{selectBookingDate(value);$('#bookingDayTitle').scrollIntoView({behavior:'smooth',block:'start'})};$('#monthGrid').append(button)
  }
}
function renderAvailability(){
  $('#availabilityList').replaceChildren();if(!dailyOverview)return;const resource=$('#bookingInstructor').value,rows=(dailyOverview.availability||[]).filter(row=>!resource||row.resource===resource);
  for(const row of rows){const card=node('article',undefined,'availability-card');card.append(node('strong',row.resource));if(!row.slots.length){card.append(node('p',row.status==='closed'?'お休み・受付枠なし':row.status==='ended'?'受付終了':'現在予約できる空き時間はありません。','hint'))}else{const ranges=[];for(const slot of row.slots){const last=ranges.at(-1);if(!row.group&&last&&last.end===slot.start)last.end=slot.end;else ranges.push({...slot})}const list=node('div',undefined,'available-ranges');for(const slot of ranges)list.append(node('span',slot.start+'〜'+slot.end+(row.group?'（残'+slot.remaining+'名）':''),'time-pill'));card.append(list)}$('#availabilityList').append(card)}
}

function dailyMessage(text,error=false){
  const node=$('#bookingMessage');node.textContent=text;node.classList.toggle('hidden',!text);node.classList.toggle('error',error);
}

async function loadDailyBookings(){
  const requestNumber=++dailyRequestNumber;
  const value=$('#bookingDate').value;
  dailyOverview=null;$('#bookingList').replaceChildren();$('#availabilityList').replaceChildren();$('#bookingStats').classList.add('hidden');$('#bookingUpdated').textContent='';
  $('#bookingDayTitle').textContent='日付を選んでください';
  if(!value){dailyMessage('確認したい日付を選んでください。');return}
  $('#bookingDayTitle').textContent=new Intl.DateTimeFormat('ja-JP',{dateStyle:'full',timeZone:'Asia/Tokyo'}).format(new Date(value+'T00:00:00+09:00'));
  dailyMessage('予約状況を確認しています…');
  $('#bookingList').setAttribute('aria-busy','true');
  try{
    const response=await fetch('/api/admin/bookings?'+new URLSearchParams({date:value}),{headers:headers(),cache:'no-store'});
    const data=await response.json();
    if(requestNumber!==dailyRequestNumber)return;
    if(!response.ok)throw new Error(response.status===401?'ログインし直してからご確認ください。':data.detail||'予約状況を取得できませんでした。');
    if(data.date!==value||!Array.isArray(data.events))throw new Error('予約状況を確認できませんでした。再読み込みしてください。');
    dailyOverview=data;renderDailyBookings();
  }catch(error){
    if(requestNumber!==dailyRequestNumber)return;
    dailyMessage(error.message||'予約状況を取得できませんでした。再読み込みしてください。',true);
  }finally{
    if(requestNumber===dailyRequestNumber)$('#bookingList').setAttribute('aria-busy','false');
  }
}

function dailyTime(value){
  const day=value.slice(0,10),hour=value.slice(11,16);
  return day===$('#bookingDate').value?hour:day.slice(5).replace('-','/')+' '+hour;
}

function renderDailyBookings(){
  if(!dailyOverview)return;
  renderAvailability();
  const resource=$('#bookingInstructor').value;
  const rows=dailyOverview.events.filter(row=>!resource||row.resource===resource);
  $('#dailyBookingCount').textContent=rows.filter(row=>row.kind==='booking'&&row.status!=='cancelled').length;
  $('#dailyCancelledCount').textContent=rows.filter(row=>row.kind==='booking'&&row.status==='cancelled').length;
  $('#dailyCalendarCount').textContent=rows.filter(row=>row.kind==='calendar').length;
  $('#bookingStats').classList.remove('hidden');
  $('#bookingUpdated').textContent='更新 '+new Intl.DateTimeFormat('ja-JP',{hour:'2-digit',minute:'2-digit',second:'2-digit',timeZone:'Asia/Tokyo',hour12:false}).format(new Date(dailyOverview.updated_at));
  const visible=rows.filter(row=>$('#showCancelledBookings').checked||row.status!=='cancelled');
  $('#bookingList').replaceChildren();
  dailyMessage(visible.length?'':rows.length?'表示する予約・予定はありません。キャンセルの表示設定もご確認ください。':'この日付・条件の予約や予定はありません。');
  for(const row of visible){
    const card=document.createElement('article');card.className='daily-card';
    if(row.status==='cancelled')card.classList.add('cancelled');
    if(row.kind==='calendar')card.classList.add('calendar');
    const time=document.createElement('div');time.className='daily-time';time.textContent=row.all_day?'終日':dailyTime(row.starts_at)+' ～\n'+dailyTime(row.ends_at);
    const content=document.createElement('div');content.className='daily-content';
    const name=document.createElement('strong');name.className='daily-name';name.textContent=row.kind==='booking'?(row.customer_name||'お名前の記録なし'):row.title;
    const detail=document.createElement('p');detail.className='daily-detail';
    detail.textContent=row.kind==='booking'?[row.menu,row.instructor==='スタジオ主催'?'スタジオ主催':row.instructor].filter(Boolean).join(' ／ '):(row.instructor||'カレンダーに直接登録された予定');
    content.append(name,detail);
    if(row.reservation_number){const number=document.createElement('p');number.className='daily-updated';number.textContent='予約番号 '+row.reservation_number;content.append(number)}
    const badge=document.createElement('span');badge.className='daily-badge';badge.textContent=row.status==='cancelled'?'キャンセル':row.kind==='calendar'?'カレンダー予定':'予約確定';
    if(row.kind==='booking'&&row.event_id){const repair=node('button','電話番号の修正・確認リンク再発行','secondary');repair.type='button';repair.onclick=()=>openBookingRecovery(row,content,repair);content.append(repair)}
    card.append(time,content,badge);$('#bookingList').append(card);
  }
}

async function openBookingRecovery(row,content,button){
  if(content.querySelector('.recovery-panel')){content.querySelector('.recovery-panel').remove();return}
  button.disabled=true;
  const panel=node('div',undefined,'recovery-panel'),message=node('p','予約情報を確認しています…','recovery-result');message.setAttribute('role','status');panel.append(message);content.append(panel);
  const url='/api/admin/bookings/'+encodeURIComponent(row.event_id)+'/recovery';
  try{
    const response=await fetch(url,{headers:headers(),cache:'no-store'}),data=await response.json();
    if(!response.ok)throw new Error(data.detail||'予約情報を取得できませんでした');
    message.textContent=[data.customer_name,LaDanzaReservations.format(data.starts_at),data.menu,data.instructor].join(' ／ ');
    const form=node('form'),label=node('label','正しい電話番号'),phone=node('input');phone.type='tel';phone.inputMode='tel';phone.required=true;phone.maxLength=30;phone.value=data.phone;label.append(phone);
    const identityLabel=node('label'),identity=node('input');identity.type='checkbox';identity.required=true;identityLabel.append(identity,document.createTextNode(' ご本人と予約内容を確認しました'));
    const note=node('p','予約日時・人数は変更しません。これまでの予約完了リンクも引き続き使えます。','hint'),save=node('button','電話番号を保存して確認リンクを発行');save.type='submit';
    const result=node('div',undefined,'recovery-result');result.setAttribute('role','status');form.append(label,identityLabel,note,save,result);panel.append(form);
    phone.oninput=()=>phone.setCustomValidity('');
    form.onsubmit=async event=>{
      event.preventDefault();phone.value=LaDanzaReservations.normalizePhone(phone.value);phone.setCustomValidity(LaDanzaReservations.validPhone(phone.value)?'':'電話番号を10桁または11桁の数字で入力してください。');if(!form.reportValidity())return;
      save.disabled=true;result.replaceChildren();save.textContent='保存しています…';
      try{
        const r=await fetch(url,{method:'POST',headers:headers(),body:JSON.stringify({phone:phone.value,identity_confirmed:identity.checked,revision:data.revision})}),body=await r.json();
        if(!r.ok)throw new Error(body.detail||'保存結果を確認できませんでした。開き直してご確認ください。');
        phone.disabled=true;identity.disabled=true;save.textContent='保存しました';
        result.append(node('p','確認リンクを発行しました。ご本人へのLINE返信などに貼り付けてご案内ください。'));
        const field=node('input');field.type='text';field.readOnly=true;field.value=body.reservation_url;field.setAttribute('aria-label','お客様の確認リンク');
        const copy=node('button','確認リンクをコピー','secondary');copy.type='button';const status=node('p');status.setAttribute('role','status');copy.onclick=()=>LaDanzaReservations.copy(body.reservation_url,field,status);result.append(field,copy,status);
      }catch(error){result.textContent=error.message;save.disabled=false;save.textContent='電話番号を保存して確認リンクを発行'}
    };
  }catch(error){message.textContent=error.message}
  finally{button.disabled=false}
}
