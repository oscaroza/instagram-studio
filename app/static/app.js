const $ = (id) => document.getElementById(id);
const fields = [
  'videoUrl','libraryId','thumbnailUrl','description','location','drone','language',
  'tone','extra','caption','hashtags','altText','hook','publicationMode'
];
const statusLabels = {
  scheduled:'Programmée', publishing:'Publication…', published:'Publiée',
  failed:'Échec', cancelled:'Annulée', awaiting_manual:'À finaliser dans Instagram'
};
let calendarCursor = new Date();
calendarCursor.setDate(1);

function setNotice(id, text, type='') {
  const el=$(id); el.textContent=text; el.className=`notice ${type}`;
}
function hideNotice(id){ $(id).className='notice hidden'; }
function formatBytes(value){
  if(!value) return '0 Mo';
  const units=['o','Ko','Mo','Go']; let amount=value,index=0;
  while(amount>=1024&&index<units.length-1){amount/=1024;index++;}
  return `${amount.toFixed(index>1?1:0)} ${units[index]}`;
}
async function api(url, options={}){
  const response=await fetch(url,options);
  if(response.status===401){window.location.href='/login';throw new Error('Session expirée.');}
  let data={}; try{data=await response.json();}catch{throw new Error(`Réponse serveur invalide (HTTP ${response.status}).`);}
  if(!data.ok) throw new Error(data.error||'Erreur serveur.');
  return data;
}

function activateTab(name){
  const tab=document.querySelector(`[data-tab="${name}"]`);
  const panel=$(name); if(!tab||!panel)return;
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  tab.classList.add('active');panel.classList.add('active');
  history.replaceState({},'',`?tab=${name}`);
  if(name==='drafts')renderDrafts();
  if(name==='settings')loadPublishingLimit();
  if(name==='library')loadLibrary();
  if(name==='calendar')loadCalendar();
  if(name==='notifications')refreshPushState();
}
for(const tab of document.querySelectorAll('.tab')){
  tab.addEventListener('click',()=>activateTab(tab.dataset.tab));
}

$('videoFile').addEventListener('change',async(e)=>{
  const file=e.target.files[0];if(!file)return;
  const form=new FormData();form.append('file',file);
  setNotice('uploadProgress',`Upload de ${file.name}…`);
  try{
    const data=await api('/api/upload',{method:'POST',body:form});
    $('videoUrl').value=data.url;
    $('libraryId').value=data.media?.id||'';
    $('thumbnailUrl').value=data.media?.thumbnail_url||'';
    setNotice('uploadProgress',`Vidéo prête avec une URL publique temporaire (${formatBytes(data.size)}).`,'success');
  }catch(err){setNotice('uploadProgress',err.message,'error');}
});

$('generateBtn').addEventListener('click',async()=>{
  const btn=$('generateBtn');btn.disabled=true;btn.textContent='Génération…';hideNotice('actionMessage');
  try{
    const payload={description:$('description').value,location:$('location').value,drone:$('drone').value,language:$('language').value,tone:$('tone').value,extra:$('extra').value};
    const data=await api('/api/ai/caption',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const x=data.result;$('caption').value=x.caption||'';$('hashtags').value=(x.hashtags||[]).join(' ');$('altText').value=x.alt_text||'';$('hook').value=x.hook||'';
    $('aiBadge').textContent=`Généré • ${data.model}`;$('aiBadge').className='pill ok';
  }catch(err){setNotice('actionMessage',err.message,'error');}
  finally{btn.disabled=false;btn.textContent='✨ Générer avec Groq';}
});

function currentDraft(){const draft={};fields.forEach(f=>draft[f]=$(f).value);draft.savedAt=new Date().toISOString();draft.id=crypto.randomUUID();return draft;}
function drafts(){try{return JSON.parse(localStorage.getItem('igstudio.drafts')||'[]');}catch{return [];}}
function saveDrafts(value){localStorage.setItem('igstudio.drafts',JSON.stringify(value));updateDraftCount();}
function updateDraftCount(){$('draftCount').textContent=drafts().length;}
$('saveDraftBtn').addEventListener('click',()=>{const list=drafts();list.unshift(currentDraft());saveDrafts(list.slice(0,50));setNotice('actionMessage','Brouillon enregistré sur cet appareil.','success');});
$('clearDraftsBtn').addEventListener('click',()=>{if(confirm('Supprimer tous les brouillons locaux ?')){saveDrafts([]);renderDrafts();}});
function loadDraft(id){const draft=drafts().find(x=>x.id===id);if(!draft)return;fields.forEach(f=>{if(draft[f]!==undefined)$(f).value=draft[f];});activateTab('composer');}
function deleteDraft(id){saveDrafts(drafts().filter(x=>x.id!==id));renderDrafts();}
function renderDrafts(){
  const list=drafts(),root=$('draftList');root.innerHTML='';
  if(!list.length){root.innerHTML='<p class="muted">Aucun brouillon.</p>';return;}
  for(const draft of list){
    const el=document.createElement('div');el.className='draft-item';
    el.innerHTML='<h3></h3><p></p><div class="draft-actions"><button class="secondary load">Ouvrir</button><button class="ghost del">Supprimer</button></div>';
    el.querySelector('h3').textContent=(draft.location||draft.description||'Brouillon').slice(0,70);
    el.querySelector('p').textContent=new Date(draft.savedAt).toLocaleString();
    el.querySelector('.load').onclick=()=>loadDraft(draft.id);el.querySelector('.del').onclick=()=>deleteDraft(draft.id);root.appendChild(el);
  }
}

function updatePublicationOptions(){
  const scheduled=$('scheduleEnabled').checked,music=$('musicEnabled').checked;
  $('scheduleFields').classList.toggle('hidden',!scheduled);
  $('musicHelp').classList.toggle('hidden',!music);
  $('publishBtn').textContent=scheduled?'Programmer le Reel':music?'Finaliser dans Instagram':'Publier le Reel';
  $('publishBtn').className=scheduled?'primary':music?'secondary':'danger';
}
$('scheduleEnabled').addEventListener('change',updatePublicationOptions);
$('musicEnabled').addEventListener('change',updatePublicationOptions);

function publicationPayload(){
  const fullCaption=[$('hook').value.trim(),$('caption').value.trim(),$('hashtags').value.trim()].filter(Boolean).join('\n\n');
  return {
    title:($('location').value||$('description').value||'Publication Instagram').trim(),
    video_url:$('videoUrl').value.trim(),library_id:$('libraryId').value,
    thumbnail_url:$('thumbnailUrl').value,caption:fullCaption,hook:$('hook').value.trim(),
    alt_text:$('altText').value.trim(),publication_mode:$('publicationMode').value,
    workflow:$('musicEnabled').checked?'manual_music':'auto_publish',
    mute_audio:$('muteAudio').checked,
    timezone:Intl.DateTimeFormat().resolvedOptions().timeZone||'Europe/Paris'
  };
}

$('publishBtn').addEventListener('click',async()=>{
  const payload=publicationPayload();
  if(!payload.video_url){setNotice('actionMessage','Ajoute une vidéo ou une URL avant de continuer.','error');return;}
  const scheduled=$('scheduleEnabled').checked,music=$('musicEnabled').checked;
  if(scheduled){
    if(!$('scheduledFor').value){setNotice('actionMessage','Choisis la date et l’heure.','error');return;}
    payload.scheduled_for=new Date($('scheduledFor').value).toISOString();
  }
  const question=scheduled?'Programmer cette publication ?':music?'Préparer ce Reel pour Instagram ?':'Publier ce Reel maintenant sur Instagram ?';
  if(!confirm(question))return;
  const btn=$('publishBtn');btn.disabled=true;const oldLabel=btn.textContent;btn.textContent='En cours…';
  try{
    if(scheduled&&!payload.library_id){
      setNotice('actionMessage','Copie durable vers Cloudinary avant programmation…');
      const promoted=await api('/api/library/promote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video_url:payload.video_url,mute_audio:payload.mute_audio})});
      payload.video_url=promoted.url;payload.library_id=promoted.media.id;payload.thumbnail_url=promoted.media.thumbnail_url||'';
      $('videoUrl').value=payload.video_url;$('libraryId').value=payload.library_id;$('thumbnailUrl').value=payload.thumbnail_url;
    }
    if(scheduled||music){
      const data=await api('/api/publications',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if(scheduled){setNotice('actionMessage','Publication programmée ✅','success');activateTab('calendar');}
      else{
        try{await navigator.clipboard.writeText(payload.caption);}catch{}
        setNotice('actionMessage','Brouillon Studio enregistré et texte copié. Ajoute maintenant la musique dans Instagram.','success');
        if(confirm('Ouvrir Instagram maintenant ?'))window.location.href='instagram://camera';
      }
      return data;
    }
    if(payload.mute_audio){
      setNotice('actionMessage','Création de la version sans son…');
      const muted=await api('/api/media/mute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video_url:payload.video_url,library_id:payload.library_id})});
      payload.video_url=muted.url;
    }
    setNotice('actionMessage','Instagram prépare la vidéo. Cela peut prendre un moment.');
    const data=await api('/api/instagram/publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    setNotice('actionMessage',`Publié ✅\nMedia ID: ${data.media_id}`,'success');
  }catch(err){setNotice('actionMessage',err.message,'error');}
  finally{btn.disabled=false;btn.textContent=oldLabel;updatePublicationOptions();}
});

async function loadPublishingLimit(){
  const target=$('publishingLimit');target.textContent='Chargement…';
  try{const data=await api('/api/instagram/publishing-limit');target.textContent=`${data.used} / ${data.total} (${data.remaining} restantes)`;target.className='cap-on';}
  catch(err){target.textContent=err.message;target.className='cap-off';}
}
$('refreshLimitBtn').addEventListener('click',loadPublishingLimit);

async function loadV2Status(){
  const pill=$('mongoStatusPill'),text=$('mongoStatusText');
  try{
    const data=await api('/api/v2/status');
    if(data.mongodb_ready){pill.textContent='MongoDB ✓';pill.className='pill ok';text.textContent='Connecté';text.className='cap-on';}
    else if(data.mongodb_configured){pill.textContent='MongoDB connexion impossible';pill.className='pill warn';text.textContent='Connexion Atlas impossible';text.className='cap-off';}
    else{pill.textContent='MongoDB à configurer';pill.className='pill warn';text.textContent='MONGODB_URI manquante';text.className='cap-off';}
  }catch(err){pill.textContent='MongoDB indisponible';pill.className='pill warn';text.textContent=err.message;text.className='cap-off';}
}

async function loadLibrary(){
  const root=$('libraryGrid');root.innerHTML='<p class="muted">Chargement…</p>';hideNotice('libraryNotice');
  try{
    const data=await api('/api/library');root.innerHTML='';
    $('libraryUsage').textContent=`${data.items.length} média(s) • ${formatBytes(data.total_bytes)} gérés par le Studio dans Cloudinary`;
    if(!data.items.length){root.innerHTML='<p class="muted">La bibliothèque est vide.</p>';return;}
    for(const item of data.items){
      const card=document.createElement('article');card.className='media-card';
      card.innerHTML='<img alt=""><div class="media-card-body"><strong></strong><span class="muted small meta"></span><div class="draft-actions"><button class="secondary use">Utiliser</button><button class="ghost delete">Supprimer</button></div></div>';
      card.querySelector('img').src=item.thumbnail_url||'/static/icons/icon-192.png';
      card.querySelector('strong').textContent=item.original_filename||'Vidéo';
      card.querySelector('.meta').textContent=`${formatBytes(item.bytes)} • ${Math.round(item.duration||0)} s`;
      card.querySelector('.use').onclick=()=>{$('videoUrl').value=item.secure_url;$('libraryId').value=item.id;$('thumbnailUrl').value=item.thumbnail_url||'';activateTab('composer');setNotice('uploadProgress','Vidéo chargée depuis la bibliothèque.','success');};
      card.querySelector('.delete').onclick=async()=>{if(!confirm(`Supprimer définitivement « ${item.original_filename||'cette vidéo'} » ?`))return;try{await api(`/api/library/${item.id}`,{method:'DELETE'});loadLibrary();}catch(err){setNotice('libraryNotice',err.message,'error');}};
      root.appendChild(card);
    }
  }catch(err){root.innerHTML='';setNotice('libraryNotice',err.message,'error');}
}
$('refreshLibrary').addEventListener('click',loadLibrary);

function monthBounds(){
  const start=new Date(calendarCursor.getFullYear(),calendarCursor.getMonth(),1);
  const end=new Date(calendarCursor.getFullYear(),calendarCursor.getMonth()+1,1);
  return {start,end};
}
function eventDate(item){return new Date(item.scheduled_for||item.published_at||item.created_at);}
async function loadCalendar(){
  const {start,end}=monthBounds();$('calendarTitle').textContent=new Intl.DateTimeFormat('fr-FR',{month:'long',year:'numeric'}).format(start);hideNotice('calendarNotice');
  try{const data=await api(`/api/publications/calendar?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`);renderCalendar(data.items,start);}
  catch(err){setNotice('calendarNotice',err.message,'error');$('calendarGrid').innerHTML='';$('calendarList').innerHTML='';}
}
function renderCalendar(items,start){
  const grid=$('calendarGrid'),list=$('calendarList');grid.innerHTML='';list.innerHTML='';
  const firstOffset=(start.getDay()+6)%7;const days=new Date(start.getFullYear(),start.getMonth()+1,0).getDate();
  for(let i=0;i<firstOffset;i++){const blank=document.createElement('div');blank.className='calendar-day empty';grid.appendChild(blank);}
  for(let day=1;day<=days;day++){
    const cell=document.createElement('div');cell.className='calendar-day';cell.innerHTML='<span class="day-number"></span><div class="day-events"></div>';cell.querySelector('.day-number').textContent=day;
    const dayItems=items.filter(item=>{const d=eventDate(item);return d.getFullYear()===start.getFullYear()&&d.getMonth()===start.getMonth()&&d.getDate()===day;});
    for(const item of dayItems){const badge=document.createElement('button');badge.className=`calendar-event status-${item.status}`;badge.textContent=item.title||statusLabels[item.status];badge.title=`${statusLabels[item.status]||item.status} • ${eventDate(item).toLocaleString()}`;badge.onclick=()=>document.getElementById(`publication-${item.id}`)?.scrollIntoView({behavior:'smooth'});cell.querySelector('.day-events').appendChild(badge);}
    grid.appendChild(cell);
  }
  if(!items.length){list.innerHTML='<p class="muted">Aucune publication ce mois-ci.</p>';return;}
  for(const item of items.sort((a,b)=>eventDate(a)-eventDate(b))){
    const row=document.createElement('article');row.className='publication-item';row.id=`publication-${item.id}`;
    row.innerHTML='<div><strong class="title"></strong><p class="muted small details"></p><p class="error-text"></p></div><div class="publication-actions"></div>';
    row.querySelector('.title').textContent=item.title||'Publication Instagram';row.querySelector('.details').textContent=`${eventDate(item).toLocaleString('fr-FR')} • ${statusLabels[item.status]||item.status}${item.publication_mode==='trial'?' • Trial Reel':''}${item.workflow==='manual_music'?' • Musique manuelle':''}`;
    if(item.last_error)row.querySelector('.error-text').textContent=item.last_error;
    if(['scheduled','failed','awaiting_manual'].includes(item.status)){const cancel=document.createElement('button');cancel.className='ghost';cancel.textContent='Annuler';cancel.onclick=async()=>{if(!confirm('Annuler cette publication ?'))return;try{await api(`/api/publications/${item.id}`,{method:'DELETE'});loadCalendar();}catch(err){setNotice('calendarNotice',err.message,'error');}};row.querySelector('.publication-actions').appendChild(cancel);}
    if(item.status==='awaiting_manual'){const copy=document.createElement('button');copy.className='secondary';copy.textContent='Copier le texte';copy.onclick=()=>navigator.clipboard.writeText(item.caption||'');row.querySelector('.publication-actions').appendChild(copy);}
    list.appendChild(row);
  }
}
$('calendarPrev').addEventListener('click',()=>{calendarCursor.setMonth(calendarCursor.getMonth()-1);loadCalendar();});
$('calendarNext').addEventListener('click',()=>{calendarCursor.setMonth(calendarCursor.getMonth()+1);loadCalendar();});

function urlBase64ToUint8Array(base64String){const padding='='.repeat((4-base64String.length%4)%4);const base64=(base64String+padding).replace(/-/g,'+').replace(/_/g,'/');const raw=atob(base64);return Uint8Array.from([...raw].map(char=>char.charCodeAt(0)));}
function pushPreferences(){return {before_publication:$('notifyBefore').checked,published:$('notifyPublished').checked,failed:$('notifyFailed').checked,manual_music:$('notifyMusic').checked};}
async function pushRegistration(){if(!('serviceWorker'in navigator))throw new Error('Service workers non pris en charge.');return navigator.serviceWorker.ready;}
async function refreshPushState(){
  if(!('Notification'in window)||!('serviceWorker'in navigator)){setNotice('pushNotice','Les notifications ne sont pas prises en charge sur ce navigateur.','error');return;}
  const registration=await pushRegistration();const subscription=await registration.pushManager.getSubscription();
  $('enablePushBtn').classList.toggle('hidden',Boolean(subscription));$('disablePushBtn').classList.toggle('hidden',!subscription);
  if(subscription)setNotice('pushNotice','Notifications actives sur cet appareil.','success');
}
$('enablePushBtn').addEventListener('click',async()=>{
  try{
    const config=await api('/api/push/config');if(!config.configured)throw new Error('Ajoute les clés VAPID dans Render.');
    const permission=await Notification.requestPermission();if(permission!=='granted')throw new Error('Autorisation de notification refusée.');
    const registration=await pushRegistration();const subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:urlBase64ToUint8Array(config.public_key)});
    await api('/api/push/subscriptions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subscription:subscription.toJSON(),preferences:pushPreferences()})});
    await refreshPushState();
  }catch(err){setNotice('pushNotice',err.message,'error');}
});
$('disablePushBtn').addEventListener('click',async()=>{try{const registration=await pushRegistration();const subscription=await registration.pushManager.getSubscription();if(subscription){await api('/api/push/subscriptions',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({endpoint:subscription.endpoint})});await subscription.unsubscribe();}await refreshPushState();}catch(err){setNotice('pushNotice',err.message,'error');}});
$('savePushPrefs').addEventListener('click',async()=>{try{const registration=await pushRegistration();const subscription=await registration.pushManager.getSubscription();if(!subscription)throw new Error('Active d’abord les notifications.');await api('/api/push/subscriptions',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({endpoint:subscription.endpoint,preferences:pushPreferences()})});setNotice('pushNotice','Préférences enregistrées.','success');}catch(err){setNotice('pushNotice',err.message,'error');}});

if('serviceWorker'in navigator){window.addEventListener('load',()=>navigator.serviceWorker.register('/sw.js').catch(()=>{}));}
updateDraftCount();updatePublicationOptions();loadV2Status();
const requestedTab=new URLSearchParams(location.search).get('tab');if(requestedTab)activateTab(requestedTab);
