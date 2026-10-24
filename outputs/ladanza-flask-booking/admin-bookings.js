let dailyOverview = null;
let dailyRequestNumber = 0;
let dailyInitialized = false;

function japanToday(){
  return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
}

function switchAdminView(view){
  const showingBookings=view==='bookings';
  $('#bookingOverview').classList.toggle('hidden',!showingBookings);
  $('#scheduleEditor').classList.toggle('hidden',showingBookings);
  $('#bookingsTab').setAttribute('aria-pressed',String(showingBookings));
  $('#scheduleTab').setAttribute('aria-pressed',String(!showingBookings));
  if(showingBookings)loadDailyBookings();
}

function initializeDailyOverview(){
  if(dailyInitialized)return;
  dailyInitialized=true;
  $('#bookingDate').value=japanToday();
  for(const name of instructors){
    const option=document.createElement('option');option.value=name;option.textContent=name;
    $('#bookingInstructor').append(option);
  }
  $('#bookingsTab').onclick=()=>switchAdminView('bookings');
  $('#scheduleTab').onclick=()=>switchAdminView('settings');
  $('#bookingDate').onchange=loadDailyBookings;
  $('#bookingRefresh').onclick=loadDailyBookings;
  $('#bookingToday').onclick=()=>{$('#bookingDate').value=japanToday();loadDailyBookings()};
  for(const [id,offset] of [['bookingPrevious',-1],['bookingNext',1]]){
    $('#'+id).onclick=()=>{
      const value=$('#bookingDate').value||japanToday();
      const day=new Date(value+'T00:00:00Z');day.setUTCDate(day.getUTCDate()+offset);
      $('#bookingDate').value=day.toISOString().slice(0,10);loadDailyBookings();
    };
  }
  $('#bookingInstructor').onchange=renderDailyBookings;
  $('#showCancelledBookings').onchange=renderDailyBookings;
}

function dailyMessage(text,error=false){
  const node=$('#bookingMessage');node.textContent=text;node.classList.toggle('hidden',!text);node.classList.toggle('error',error);
}

async function loadDailyBookings(){
  const requestNumber=++dailyRequestNumber;
  const value=$('#bookingDate').value;
  dailyOverview=null;$('#bookingList').replaceChildren();$('#bookingStats').classList.add('hidden');$('#bookingUpdated').textContent='';
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
    card.append(time,content,badge);$('#bookingList').append(card);
  }
}
