const $ = (id) => document.getElementById(id);
const fields = [
  'mediaKind','mediaItemsJson','videoUrl','libraryId','thumbnailUrl','description','location','drone','language',
  'tone','extra','caption','hashtags','altText','hook','publicationMode'
];
const statusLabels = {
  scheduled:'Programmée', publishing:'Publication…', published:'Publiée',
  failed:'Échec', cancelled:'Annulée', awaiting_manual:'À finaliser dans Instagram'
};
let calendarCursor = new Date();
calendarCursor.setDate(1);
let selectedMediaItems = [];
let analyticsPosts = [];
let preparedInstagramShare = null;
const SESSION_IDLE_MS = Math.max(60,Number(document.body.dataset.sessionIdleSeconds)||300) * 1000;
let lastActivityAt = Date.now();
let lastSessionTouchAt = 0;
let sessionIdleTimer = null;
let studioSessionExpired = false;
const STUDIO_SOUND_STORAGE_KEY = 'igstudio.studioSoundEnabled';
let studioAudioContext = null;

function studioSoundEnabled(){
  try{return localStorage.getItem(STUDIO_SOUND_STORAGE_KEY)!=='false';}catch{return true;}
}
function setStudioSoundEnabled(enabled){
  try{localStorage.setItem(STUDIO_SOUND_STORAGE_KEY,String(enabled));}catch{}
}
function studioAudio(){
  const AudioContextClass=window.AudioContext||window.webkitAudioContext;
  if(!AudioContextClass)return null;
  if(!studioAudioContext)studioAudioContext=new AudioContextClass();
  return studioAudioContext;
}
function unlockStudioSound(){
  if(!studioSoundEnabled())return;
  const context=studioAudio();
  if(context?.state==='suspended')context.resume().catch(()=>{});
}
function playStudioChime(){
  if(!studioSoundEnabled())return;
  const context=studioAudio();
  if(!context)return;
  const play=()=>{
    const start=context.currentTime+0.02;
    const master=context.createGain();
    master.gain.setValueAtTime(0.65,start);
    master.connect(context.destination);
    [
      {frequency:783.99,offset:0,duration:0.24,volume:0.11},
      {frequency:1174.66,offset:0.10,duration:0.38,volume:0.12}
    ].forEach(note=>{
      const oscillator=context.createOscillator();
      const gain=context.createGain();
      const noteStart=start+note.offset;
      oscillator.type='sine';
      oscillator.frequency.setValueAtTime(note.frequency,noteStart);
      oscillator.frequency.exponentialRampToValueAtTime(note.frequency*1.035,noteStart+note.duration);
      gain.gain.setValueAtTime(0.0001,noteStart);
      gain.gain.exponentialRampToValueAtTime(note.volume,noteStart+0.025);
      gain.gain.exponentialRampToValueAtTime(0.0001,noteStart+note.duration);
      oscillator.connect(gain);gain.connect(master);
      oscillator.start(noteStart);oscillator.stop(noteStart+note.duration+0.02);
    });
  };
  if(context.state==='suspended')context.resume().then(play).catch(()=>{});
  else play();
}
function refreshStudioSoundSetting(){
  const enabled=studioSoundEnabled();
  $('studioSoundEnabled').checked=enabled;
  $('studioSoundStatus').textContent=enabled?'Activé sur cet appareil':'Désactivé sur cet appareil';
  $('studioSoundStatus').className=enabled?'cap-on':'cap-off';
}

function expireStudioSession(){
  if(studioSessionExpired)return;
  studioSessionExpired=true;
  clearTimeout(sessionIdleTimer);
  $('sessionExpiredBanner').classList.remove('hidden');
  document.body.classList.add('session-expired');
}
function scheduleSessionExpiry(){
  clearTimeout(sessionIdleTimer);
  const remaining=Math.max(0,SESSION_IDLE_MS-(Date.now()-lastActivityAt));
  sessionIdleTimer=setTimeout(expireStudioSession,remaining);
}
async function touchStudioSession(){
  const now=Date.now();
  if(studioSessionExpired||now-lastSessionTouchAt<30000)return;
  lastSessionTouchAt=now;
  try{
    const response=await fetch('/api/session/touch',{method:'POST'});
    if(response.status===401)expireStudioSession();
  }catch{}
}
function registerActivity(){
  if(studioSessionExpired||document.visibilityState==='hidden')return;
  lastActivityAt=Date.now();
  scheduleSessionExpiry();
  touchStudioSession();
}
for(const eventName of ['pointerdown','keydown','touchstart','scroll']){
  window.addEventListener(eventName,registerActivity,{passive:true});
}
window.addEventListener('pointerdown',unlockStudioSound,{passive:true});
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState!=='visible')return;
  if(Date.now()-lastActivityAt>=SESSION_IDLE_MS)expireStudioSession();
  else registerActivity();
});
$('refreshSessionBtn').addEventListener('click',()=>location.reload());
scheduleSessionExpiry();

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
  if(response.status===401){expireStudioSession();throw new Error('Session expirée. Actualise la page pour te reconnecter.');}
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
  if(name==='settings'){loadPublishingLimit();loadInstagramTokenHealth();loadLoginHistory();}
  if(name==='library')loadLibrary();
  if(name==='calendar')loadCalendar();
  if(name==='stats')loadAnalytics();
  if(name==='notifications')refreshPushState();
}
for(const tab of document.querySelectorAll('.tab')){
  tab.addEventListener('click',()=>activateTab(tab.dataset.tab));
}

function syncMediaFields(){
  $('mediaItemsJson').value=JSON.stringify(selectedMediaItems);
  const first=selectedMediaItems[0]||{};
  $('videoUrl').value=selectedMediaItems.length===1?(first.url||''):'';
  $('libraryId').value=selectedMediaItems.length===1?(first.library_id||''):'';
  $('thumbnailUrl').value=first.thumbnail_url||'';
}
function renderSelectedMedia(){
  const root=$('selectedMedia');root.innerHTML='';
  selectedMediaItems.forEach((item,index)=>{
    const row=document.createElement('div');row.className='selected-media-item';
    row.innerHTML='<img alt=""><div><strong></strong><span></span></div><button class="ghost" type="button">Retirer</button>';
    row.querySelector('img').src=item.thumbnail_url||item.url||'/static/icons/icon-192.png';
    row.querySelector('strong').textContent=item.name||`Média ${index+1}`;
    row.querySelector('span').textContent=`${item.media_type==='image'?'Photo JPEG':'Vidéo'}${item.size?` • ${formatBytes(item.size)}`:''}`;
    row.querySelector('button').onclick=()=>{selectedMediaItems.splice(index,1);syncMediaFields();renderSelectedMedia();};
    root.appendChild(row);
  });
}
function clearPreparedInstagramShare(){
  preparedInstagramShare=null;
  $('instagramSharePanel').classList.add('hidden');
}

function shareFileName(item,index){
  const isImage=item.media_type==='image';
  const fallback=isImage?'photo.jpg':'video.mp4';
  const source=(item.name||new URL(item.url,location.href).pathname.split('/').pop()||fallback).split('?')[0];
  const safe=source.replace(/[^a-zA-Z0-9._-]+/g,'-').replace(/^-+|-+$/g,'')||fallback;
  const hasExtension=/\.[a-zA-Z0-9]{2,5}$/.test(safe);
  return `${String(index+1).padStart(2,'0')}-${hasExtension?safe:`${safe}.${isImage?'jpg':'mp4'}`}`;
}
async function copyInstagramCaption(caption,noticeId='actionMessage'){
  try{
    await navigator.clipboard.writeText(caption||'');
    return true;
  }catch{
    if(noticeId)setNotice(noticeId,'Les médias sont prêts, mais la copie automatique a été bloquée. Utilise « Copier le texte » avant d’ouvrir Instagram.','error');
    return false;
  }
}
async function fetchInstagramShareFiles(items,noticeId='actionMessage'){
  const files=[];
  for(let index=0;index<items.length;index++){
    const item=items[index];
    if(noticeId)setNotice(noticeId,`Préparation du média ${index+1}/${items.length}…`);
    const response=await fetch(item.url,{credentials:new URL(item.url,location.href).origin===location.origin?'same-origin':'omit'});
    if(!response.ok)throw new Error(`Impossible de préparer le média ${index+1}.`);
    const blob=await response.blob();
    const type=item.media_type==='image'?'image/jpeg':(blob.type&&blob.type!=='application/octet-stream'?blob.type:'video/mp4');
    files.push(new File([blob],shareFileName(item,index),{type,lastModified:Date.now()}));
  }
  return files;
}
function canNativeShareFiles(files){
  if(!files.length||!navigator.share)return false;
  try{return !navigator.canShare||navigator.canShare({files});}catch{return false;}
}
function renderInstagramSharePanel(payload,files=[],preparationError='',captionCopied=false){
  preparedInstagramShare={payload,files,captionCopied};
  const panel=$('instagramSharePanel'),preview=$('instagramSharePreview'),fallback=$('instagramShareFallback');
  preview.innerHTML='';fallback.innerHTML='';
  for(const [index,item] of (payload.media_items||[]).entries()){
    const media=document.createElement(item.media_type==='image'?'img':'video');
    media.src=item.thumbnail_url||item.url;media.alt=`Média ${index+1}`;if(media.tagName==='VIDEO')media.muted=true;
    const figure=document.createElement('figure');figure.innerHTML='<span></span>';figure.querySelector('span').textContent=index+1;figure.prepend(media);preview.appendChild(figure);
    const link=document.createElement('a');link.className='ghost';link.href=item.url;link.target='_blank';link.rel='noopener';link.textContent=`Ouvrir / enregistrer ${item.media_type==='image'?'la photo':'la vidéo'} ${index+1}`;fallback.appendChild(link);
  }
  const count=(payload.media_items||[]).length;
  $('instagramShareTitle').textContent=`${count} média${count>1?'s':''} prêt${count>1?'s':''} dans le bon ordre`;
  const canShare=canNativeShareFiles(files);
  $('shareMediaBtn').classList.toggle('hidden',!canShare);
  $('shareMediaBtn').textContent=`Partager ${count>1?'les médias':'le média'} vers Instagram`;
  $('instagramShareHelp').textContent=preparationError
    ?`${preparationError} Utilise les boutons de secours ci-dessous.`
    :canShare
      ?`Appuie ci-dessous, puis choisis Instagram dans le menu de l’iPhone. ${captionCopied?'La légende est déjà copiée.':'Utilise aussi « Copier le texte » pour la légende.'}`
      :'Le partage groupé n’est pas disponible sur cet appareil. Enregistre les médias ci-dessous dans l’ordre, puis ouvre Instagram.';
  panel.classList.remove('hidden');
  panel.scrollIntoView({behavior:'smooth',block:'center'});
}
async function prepareInstagramFinalization(payload,noticeId='actionMessage'){
  preparedInstagramShare=null;
  const copied=await copyInstagramCaption(payload.caption||'',noticeId);
  try{
    const files=await fetchInstagramShareFiles(payload.media_items||[],noticeId);
    renderInstagramSharePanel(payload,files,'',copied);
    if(noticeId)setNotice(noticeId,`Brouillon Studio enregistré • médias prêts${copied?' • texte copié':''}.`,'success');
  }catch(error){
    renderInstagramSharePanel(payload,[],error.message,copied);
    if(noticeId)setNotice(noticeId,`${error.message} Les liens de secours restent disponibles.`,'error');
  }
}
$('shareMediaBtn').addEventListener('click',async()=>{
  const prepared=preparedInstagramShare;if(!prepared?.files?.length)return;
  try{
    await navigator.share({files:prepared.files,title:prepared.payload.title||'Instagram Studio'});
    setNotice('actionMessage','Médias envoyés au menu de partage. Dans Instagram, ajoute la musique puis colle la légende.','success');
  }catch(error){
    if(error.name!=='AbortError')setNotice('actionMessage','Le partage groupé a été refusé. Utilise les boutons de secours affichés sous les médias.','error');
  }
});
$('copyPreparedCaptionBtn').addEventListener('click',async()=>{
  if(await copyInstagramCaption(preparedInstagramShare?.payload?.caption||'',null))setNotice('actionMessage','Texte copié ✅','success');
  else setNotice('actionMessage','Copie impossible. Sélectionne manuellement la caption dans le Studio.','error');
});
$('openInstagramFallbackBtn').addEventListener('click',()=>{location.href='instagram://camera';});
function clearMediaSelection(){
  clearPreparedInstagramShare();selectedMediaItems=[];$('videoFile').value='';$('videoUrl').value='';$('libraryId').value='';$('thumbnailUrl').value='';syncMediaFields();renderSelectedMedia();hideNotice('uploadProgress');
}
function configureMediaKind(clear=true){
  const kind=$('mediaKind').value,file=$('videoFile');
  if(clear)clearMediaSelection();
  if(kind==='reel'){
    file.accept='video/mp4,video/quicktime,video/x-m4v';file.multiple=false;
    $('mediaUploadLabel').textContent='Choisir une vidéo';$('mediaUploadHelp').textContent='MP4 / MOV / M4V';$('mediaUrlLabel').textContent='URL vidéo publique';
  }else{
    file.accept='image/jpeg';file.multiple=kind==='carousel';
    $('mediaUploadLabel').textContent=kind==='carousel'?'Choisir 2 à 10 photos':'Choisir une photo';$('mediaUploadHelp').textContent='JPG / JPEG • 8 Mo max par photo';$('mediaUrlLabel').textContent='URL photo JPEG publique';
  }
  $('publicUrlBlock').classList.toggle('hidden',kind==='carousel');
  $('publicationModeField').classList.toggle('hidden',kind!=='reel');
  $('muteOption').classList.toggle('hidden',kind!=='reel');
  if(kind!=='reel'){$('publicationMode').value='normal';$('muteAudio').checked=false;}
  updatePublicationOptions();
}
$('mediaKind').addEventListener('change',()=>configureMediaKind(true));
$('videoFile').addEventListener('change',async(e)=>{
  const files=[...e.target.files];if(!files.length)return;
  const kind=$('mediaKind').value,expected=kind==='reel'?'video':'image';
  if(kind==='carousel'&&selectedMediaItems.length+files.length>10){setNotice('uploadProgress','Un carrousel contient au maximum 10 photos.','error');return;}
  if(kind!=='carousel')selectedMediaItems=[];
  try{
    for(const file of files){
      setNotice('uploadProgress',`Upload de ${file.name}…`);
      const form=new FormData();form.append('file',file);
      const data=await api('/api/upload',{method:'POST',body:form});
      if(data.media_type!==expected)throw new Error('Le fichier ne correspond pas au type de publication choisi.');
      selectedMediaItems.push({url:data.url,library_id:'',thumbnail_url:data.media_type==='image'?data.url:'',media_type:data.media_type,name:file.name,size:data.size});
    }
    syncMediaFields();renderSelectedMedia();
    const label=kind==='carousel'?`${selectedMediaItems.length} photos prêtes`:`${kind==='photo'?'Photo':'Vidéo'} prête`;
    setNotice('uploadProgress',`${label} avec une URL publique temporaire.`,'success');
  }catch(err){setNotice('uploadProgress',err.message,'error');}
  finally{e.target.value='';}
});
$('videoUrl').addEventListener('input',()=>{
  if(selectedMediaItems.length){selectedMediaItems=[];$('libraryId').value='';$('thumbnailUrl').value='';$('mediaItemsJson').value='[]';renderSelectedMedia();}
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

function currentDraft(){syncMediaFields();const draft={};fields.forEach(f=>draft[f]=$(f).value);draft.savedAt=new Date().toISOString();draft.id=crypto.randomUUID();return draft;}
function drafts(){try{return JSON.parse(localStorage.getItem('igstudio.drafts')||'[]');}catch{return [];}}
function saveDrafts(value){localStorage.setItem('igstudio.drafts',JSON.stringify(value));updateDraftCount();}
function updateDraftCount(){$('draftCount').textContent=drafts().length;}
$('saveDraftBtn').addEventListener('click',()=>{const list=drafts();list.unshift(currentDraft());saveDrafts(list.slice(0,50));setNotice('actionMessage','Brouillon enregistré sur cet appareil.','success');});
$('clearDraftsBtn').addEventListener('click',()=>{if(confirm('Supprimer tous les brouillons locaux ?')){saveDrafts([]);renderDrafts();}});
function loadDraft(id){
  const draft=drafts().find(x=>x.id===id);if(!draft)return;
  clearPreparedInstagramShare();
  fields.forEach(f=>{if(draft[f]!==undefined)$(f).value=draft[f];});
  try{selectedMediaItems=JSON.parse(draft.mediaItemsJson||'[]');}catch{selectedMediaItems=[];}
  configureMediaKind(false);syncMediaFields();renderSelectedMedia();activateTab('composer');
}
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
  const scheduled=$('scheduleEnabled').checked,music=$('musicEnabled').checked,kind=$('mediaKind').value;
  const typeLabel={reel:'le Reel',photo:'la photo',carousel:'le carrousel'}[kind];
  $('scheduleFields').classList.toggle('hidden',!scheduled);
  $('musicHelp').classList.toggle('hidden',!music);
  $('publishBtn').textContent=scheduled?`Programmer ${typeLabel}`:music?'Finaliser dans Instagram':`Publier ${typeLabel}`;
  $('publishBtn').className=scheduled?'primary':music?'secondary':'danger';
}
$('scheduleEnabled').addEventListener('change',updatePublicationOptions);
$('musicEnabled').addEventListener('change',updatePublicationOptions);

function publicationPayload(){
  const fullCaption=[$('hook').value.trim(),$('caption').value.trim(),$('hashtags').value.trim()].filter(Boolean).join('\n\n');
  const mediaKind=$('mediaKind').value;
  let items=selectedMediaItems.map(item=>({...item}));
  const publicUrl=$('videoUrl').value.trim();
  if(mediaKind!=='carousel'&&publicUrl&&(!items.length||items[0].url!==publicUrl)){
    items=[{url:publicUrl,library_id:$('libraryId').value,thumbnail_url:$('thumbnailUrl').value,media_type:mediaKind==='reel'?'video':'image',name:mediaKind==='reel'?'Vidéo par URL':'Photo par URL'}];
  }
  return {
    title:($('location').value||$('description').value||'Publication Instagram').trim(),
    media_kind:mediaKind,media_items:items,
    video_url:mediaKind==='reel'?(items[0]?.url||''):'',image_url:mediaKind==='photo'?(items[0]?.url||''):'',library_id:items[0]?.library_id||'',
    thumbnail_url:$('thumbnailUrl').value,caption:fullCaption,hook:$('hook').value.trim(),
    alt_text:$('altText').value.trim(),publication_mode:$('publicationMode').value,
    workflow:$('musicEnabled').checked?'manual_music':'auto_publish',
    mute_audio:$('muteAudio').checked,
    timezone:Intl.DateTimeFormat().resolvedOptions().timeZone||'Europe/Paris'
  };
}

$('publishBtn').addEventListener('click',async()=>{
  const payload=publicationPayload();
  if(payload.media_kind==='carousel'&&(payload.media_items.length<2||payload.media_items.length>10)){setNotice('actionMessage','Ajoute entre 2 et 10 photos JPEG au carrousel.','error');return;}
  if(payload.media_kind!=='carousel'&&payload.media_items.length!==1){setNotice('actionMessage','Ajoute un média ou une URL avant de continuer.','error');return;}
  const scheduled=$('scheduleEnabled').checked,music=$('musicEnabled').checked;
  if(scheduled){
    if(!$('scheduledFor').value){setNotice('actionMessage','Choisis la date et l’heure.','error');return;}
    payload.scheduled_for=new Date($('scheduledFor').value).toISOString();
  }
  const kindLabel={reel:'ce Reel',photo:'cette photo',carousel:'ce carrousel'}[payload.media_kind];
  const question=scheduled?'Programmer cette publication ?':music?`Préparer ${kindLabel} pour Instagram ?`:`Publier ${kindLabel} maintenant sur Instagram ?`;
  const btn=$('publishBtn');btn.disabled=true;const oldLabel=btn.textContent;btn.textContent='Vérification…';hideNotice('preflightNotice');
  try{
    const preflight=await api('/api/publications/preflight',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    setNotice('preflightNotice',`Vérification réussie ✅\n${(preflight.checks||[]).map(check=>`• ${check}`).join('\n')}`,'success');
    if(!confirm(question))return;
    btn.textContent='En cours…';
    if(scheduled){
      const durableItems=payload.media_items.map(item=>({...item}));
      for(let index=0;index<payload.media_items.length;index++){
        const item=durableItems[index];
        if(item.library_id)continue;
        setNotice('actionMessage',`Copie durable vers Cloudinary (${index+1}/${payload.media_items.length})…`);
        const promoted=await api('/api/library/promote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_url:item.url,media_type:item.media_type,mute_audio:payload.mute_audio})});
        durableItems[index]={...item,url:promoted.url,library_id:promoted.media.id,thumbnail_url:promoted.media.thumbnail_url||item.thumbnail_url};
        payload.media_items=durableItems;selectedMediaItems=durableItems.map(mediaItem=>({...mediaItem}));syncMediaFields();renderSelectedMedia();
      }
      payload.media_items=durableItems;selectedMediaItems=durableItems.map(item=>({...item}));syncMediaFields();renderSelectedMedia();
      payload.library_id=durableItems.length===1?durableItems[0].library_id:'';
      payload.thumbnail_url=durableItems[0]?.thumbnail_url||'';
      if(payload.media_kind==='reel')payload.video_url=durableItems[0].url;
      if(payload.media_kind==='photo')payload.image_url=durableItems[0].url;
    }
    if(scheduled||music){
      const data=await api('/api/publications',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if(scheduled){setNotice('actionMessage','Publication programmée ✅','success');playStudioChime();activateTab('calendar');}
      else{
        await prepareInstagramFinalization(payload);
      }
      return data;
    }
    if(payload.media_kind==='reel'&&payload.mute_audio){
      setNotice('actionMessage','Création de la version sans son…');
      const muted=await api('/api/media/mute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video_url:payload.video_url,library_id:payload.library_id})});
      payload.video_url=muted.url;
      payload.media_items[0].url=muted.url;
    }
    setNotice('actionMessage','Instagram prépare la publication. Cela peut prendre un moment.');
    const data=await api('/api/instagram/publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    setNotice('actionMessage',`Publié ✅\nMedia ID: ${data.media_id}`,'success');
    playStudioChime();
  }catch(err){setNotice('actionMessage',err.message,'error');}
  finally{btn.disabled=false;btn.textContent=oldLabel;updatePublicationOptions();}
});

async function loadPublishingLimit(){
  const target=$('publishingLimit');target.textContent='Chargement…';
  try{const data=await api('/api/instagram/publishing-limit');target.textContent=`${data.used} / ${data.total} (${data.remaining} restantes)`;target.className='cap-on';}
  catch(err){target.textContent=err.message;target.className='cap-off';}
}
$('refreshLimitBtn').addEventListener('click',loadPublishingLimit);
async function loadInstagramTokenHealth(){
  const target=$('instagramTokenHealth');target.textContent='Chargement…';target.className='';
  try{
    const data=await api('/api/instagram/token-health');
    if(!data.configured){target.textContent='Non connecté';target.className='cap-off';return;}
    if(data.refresh_error){target.textContent='Renouvellement à vérifier';target.className='cap-off';return;}
    if(data.days_remaining!==null){target.textContent=`Actif • environ ${data.days_remaining} jour(s) restant(s)`;target.className=data.days_remaining<=7?'cap-off':'cap-on';return;}
    target.textContent='Configuré • expiration inconnue';target.className='cap-on';
  }catch(err){target.textContent=err.message;target.className='cap-off';}
}
async function loadLoginHistory(){
  const root=$('loginHistory');
  root.innerHTML='<p class="muted">Chargement…</p>';hideNotice('loginHistoryNotice');
  try{
    const data=await api('/api/security/login-history');root.innerHTML='';
    if(!data.events.length){root.innerHTML='<p class="muted">Aucune connexion enregistrée pour le moment.</p>';return;}
    for(const event of data.events){
      const row=document.createElement('div');row.className='draft-item';
      const date=new Date(event.created_at).toLocaleString('fr-FR',{dateStyle:'medium',timeStyle:'short'});
      row.innerHTML='<div class="row-between"><strong></strong><span class="pill"></span></div><p></p>';
      row.querySelector('strong').textContent=`${event.device} • ${event.browser}`;
      const status=row.querySelector('.pill');status.textContent=event.success?'Connexion réussie':'Essai refusé';status.classList.add(event.success?'ok':'warn');
      row.querySelector('p').textContent=date;root.appendChild(row);
    }
  }catch(err){root.innerHTML='';setNotice('loginHistoryNotice',err.message,'error');}
}
$('refreshLoginHistory').addEventListener('click',loadLoginHistory);
$('studioSoundEnabled').addEventListener('change',(event)=>{
  setStudioSoundEnabled(event.target.checked);refreshStudioSoundSetting();
  if(event.target.checked)playStudioChime();
});
$('testStudioSoundBtn').addEventListener('click',playStudioChime);

async function loadV2Status(){
  const pill=$('mongoStatusPill'),text=$('mongoStatusText'),cloudPill=$('cloudinaryStatusPill'),cloudText=$('cloudinaryStatusText');
  try{
    const data=await api('/api/v2/status');
    if(data.mongodb_ready){pill.textContent='MongoDB ✓';pill.className='pill ok';text.textContent='Connecté';text.className='cap-on';}
    else if(data.mongodb_configured){pill.textContent='MongoDB connexion impossible';pill.className='pill warn';text.textContent='Connexion Atlas impossible';text.className='cap-off';}
    else{pill.textContent='MongoDB à configurer';pill.className='pill warn';text.textContent='MONGODB_URI manquante';text.className='cap-off';}
    if(data.cloudinary_ready){cloudPill.textContent='Cloudinary ✓';cloudPill.className='pill ok';cloudText.textContent='Connecté';cloudText.className='cap-on';}
    else if(data.cloudinary_configured){cloudPill.textContent='Cloudinary connexion refusée';cloudPill.className='pill warn';cloudText.textContent=data.cloudinary_error||'Connexion impossible';cloudText.className='cap-off';}
    else{cloudPill.textContent='Cloudinary à configurer';cloudPill.className='pill warn';cloudText.textContent='Variables Cloudinary manquantes';cloudText.className='cap-off';}
  }catch(err){
    pill.textContent='MongoDB indisponible';pill.className='pill warn';text.textContent=err.message;text.className='cap-off';
    cloudPill.textContent='Cloudinary indisponible';cloudPill.className='pill warn';cloudText.textContent=err.message;cloudText.className='cap-off';
  }
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
      const itemType=item.media_type||item.resource_type||'video';
      card.querySelector('strong').textContent=item.original_filename||(itemType==='image'?'Photo':'Vidéo');
      card.querySelector('.meta').textContent=itemType==='image'?`${formatBytes(item.bytes)} • ${item.width||'?'} × ${item.height||'?'}`:`${formatBytes(item.bytes)} • ${Math.round(item.duration||0)} s`;
      card.querySelector('.use').onclick=()=>{
        const mediaItem={url:item.secure_url,library_id:item.id,thumbnail_url:item.thumbnail_url||'',media_type:itemType,name:item.original_filename||'Média',size:item.bytes||0};
        if(itemType==='image'&&$('mediaKind').value==='carousel'){
          if(selectedMediaItems.length>=10){setNotice('libraryNotice','Le carrousel contient déjà 10 photos.','error');return;}
          selectedMediaItems.push(mediaItem);
        }else{
          $('mediaKind').value=itemType==='image'?'photo':'reel';configureMediaKind(true);selectedMediaItems=[mediaItem];
        }
        syncMediaFields();renderSelectedMedia();activateTab('composer');setNotice('uploadProgress',`${itemType==='image'?'Photo':'Vidéo'} chargée depuis la bibliothèque.`,'success');
      };
      card.querySelector('.delete').onclick=async()=>{if(!confirm(`Supprimer définitivement « ${item.original_filename||'ce média'} » ?`))return;try{await api(`/api/library/${item.id}`,{method:'DELETE'});loadLibrary();}catch(err){setNotice('libraryNotice',err.message,'error');}};
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
    const mediaLabel={photo:'Photo',carousel:'Carrousel',reel:'Reel'}[item.media_kind||'reel'];
    row.querySelector('.title').textContent=item.title||'Publication Instagram';row.querySelector('.details').textContent=`${eventDate(item).toLocaleString('fr-FR')} • ${mediaLabel} • ${statusLabels[item.status]||item.status}${item.publication_mode==='trial'?' • Trial Reel':''}${item.workflow==='manual_music'?' • Musique manuelle':''}`;
    if(item.last_error)row.querySelector('.error-text').textContent=item.last_error;
    if(['scheduled','failed','awaiting_manual'].includes(item.status)){const cancel=document.createElement('button');cancel.className='ghost';cancel.textContent='Annuler';cancel.onclick=async()=>{if(!confirm('Annuler cette publication ?'))return;try{await api(`/api/publications/${item.id}`,{method:'DELETE'});loadCalendar();}catch(err){setNotice('calendarNotice',err.message,'error');}};row.querySelector('.publication-actions').appendChild(cancel);}
    if(item.status==='awaiting_manual'){
      const prepare=document.createElement('button');prepare.className='primary';prepare.textContent='Préparer les médias';prepare.onclick=async()=>{prepare.disabled=true;prepare.textContent='Préparation…';activateTab('composer');await prepareInstagramFinalization(item);prepare.disabled=false;prepare.textContent='Préparer les médias';};row.querySelector('.publication-actions').appendChild(prepare);
      const copy=document.createElement('button');copy.className='secondary';copy.textContent='Copier le texte';copy.onclick=()=>copyInstagramCaption(item.caption||'','calendarNotice');row.querySelector('.publication-actions').appendChild(copy);
    }
    list.appendChild(row);
  }
}
$('calendarPrev').addEventListener('click',()=>{calendarCursor.setMonth(calendarCursor.getMonth()-1);loadCalendar();});
$('calendarNext').addEventListener('click',()=>{calendarCursor.setMonth(calendarCursor.getMonth()+1);loadCalendar();});

function formatStat(value){return new Intl.NumberFormat('fr-FR',{notation:Number(value)>=10000?'compact':'standard',maximumFractionDigits:1}).format(Number(value)||0);}
function analyticsPostLabel(item){return item.hook||item.title||'Sans hook enregistré';}
function sortedAnalyticsPosts(){
  const items=[...analyticsPosts];
  const mode=$('statsSort').value;
  const numeric=(item,key)=>Number(item[key])||0;
  const timestamp=(item)=>{const value=Date.parse(item.timestamp||'');return Number.isNaN(value)?0:value;};
  const alphabetic=(a,b)=>analyticsPostLabel(a).localeCompare(analyticsPostLabel(b),'fr',{sensitivity:'base'});
  const newestFirst=(a,b)=>timestamp(b)-timestamp(a);
  const comparators={
    views_desc:(a,b)=>numeric(b,'views')-numeric(a,'views')||newestFirst(a,b),
    views_asc:(a,b)=>numeric(a,'views')-numeric(b,'views')||newestFirst(a,b),
    likes_desc:(a,b)=>numeric(b,'likes')-numeric(a,'likes')||newestFirst(a,b),
    likes_asc:(a,b)=>numeric(a,'likes')-numeric(b,'likes')||newestFirst(a,b),
    engagement_desc:(a,b)=>numeric(b,'engagement_rate')-numeric(a,'engagement_rate')||newestFirst(a,b),
    engagement_asc:(a,b)=>numeric(a,'engagement_rate')-numeric(b,'engagement_rate')||newestFirst(a,b),
    date_desc:newestFirst,
    date_asc:(a,b)=>timestamp(a)-timestamp(b),
    alpha_asc:alphabetic,
    alpha_desc:(a,b)=>alphabetic(b,a)
  };
  return items.sort(comparators[mode]||comparators.views_desc);
}
function renderAnalyticsPosts(){
  const posts=$('statsPosts');posts.innerHTML='';
  for(const item of sortedAnalyticsPosts()){
    const row=document.createElement('article');row.className='stats-post';row.innerHTML='<div class="stats-post-main"><span class="pill kind"></span><strong class="hook"></strong><span class="date"></span></div><div class="stats-post-metrics"><span class="views"></span><span class="likes"></span><span class="reach"></span><span class="rate"></span><span class="delta"></span></div><a class="ghost permalink" target="_blank" rel="noopener">Voir</a>';
    row.querySelector('.kind').textContent={reel:'Reel',photo:'Photo',carousel:'Carrousel'}[item.media_kind]||'Post';
    row.querySelector('.hook').textContent=analyticsPostLabel(item);
    row.querySelector('.date').textContent=item.timestamp?new Date(item.timestamp).toLocaleString('fr-FR'):'Date indisponible';
    row.querySelector('.views').textContent=`${formatStat(item.views)} vues`;
    row.querySelector('.likes').textContent=`${formatStat(item.likes)} likes`;
    row.querySelector('.reach').textContent=`${formatStat(item.reach)} portée`;
    row.querySelector('.rate').textContent=`${Number(item.engagement_rate||0).toFixed(1)} % engagement`;
    const delta=Number(item.delta_views||0);row.querySelector('.delta').textContent=delta?`${delta>0?'+':''}${formatStat(delta)} vues depuis le relevé précédent`:'Premier relevé';
    const link=row.querySelector('.permalink');if(item.permalink)link.href=item.permalink;else link.classList.add('hidden');posts.appendChild(row);
  }
  if(!posts.children.length)posts.innerHTML='<p class="muted">Aucune publication synchronisée.</p>';
}
function renderAssistantReport(report,createdAt=''){
  const root=$('statsAssistantReport');root.innerHTML='';
  if(!report){root.innerHTML='<p class="muted">Synchronise au moins 3 publications, puis lance l’analyse.</p>';$('statsAssistantMeta').textContent='';return;}
  const summary=document.createElement('p');summary.className='assistant-summary';summary.textContent=report.summary||'Analyse terminée.';root.appendChild(summary);
  const sections=[
    ['Recommandations',report.recommendations],
    ['Hooks',report.hook_findings],
    ['Heures de publication',report.timing_findings],
    ['Tests à essayer',report.experiments],
    ['À garder en tête',report.cautions]
  ];
  for(const [title,values] of sections){
    if(!Array.isArray(values)||!values.length)continue;
    const section=document.createElement('section');section.innerHTML='<h3></h3><ul></ul>';section.querySelector('h3').textContent=title;
    for(const value of values){const item=document.createElement('li');item.textContent=value;section.querySelector('ul').appendChild(item);}root.appendChild(section);
  }
  $('statsAssistantMeta').textContent=createdAt?`Dernière analyse : ${new Date(createdAt).toLocaleString('fr-FR')}`:'Analyse Groq enregistrée dans le Studio.';
}
function renderAnalytics(data){
  const summary=data.summary||{};
  $('statsMediaCount').textContent=formatStat(summary.media_count);
  $('statsMediaKinds').textContent=`${summary.reels||0} Reel(s) • ${summary.photos||0} photo(s) • ${summary.carousels||0} carrousel(s)`;
  $('statsViews').textContent=formatStat(summary.views);
  $('statsReach').textContent=formatStat(summary.reach);
  $('statsEngagement').textContent=`${Number(summary.engagement_rate||0).toFixed(1)} %`;
  $('statsInteractions').textContent=`${formatStat(summary.interactions)} interaction(s)`;
  const sync=data.sync||{};
  $('statsSyncMeta').textContent=sync.last_synced_at?`Dernier relevé : ${new Date(sync.last_synced_at).toLocaleString('fr-FR')} • ${sync.metrics_updated||0} publication(s) mise(s) à jour`:'Aucune synchronisation enregistrée.';
  if(sync.permission_required&&sync.last_error)setNotice('statsNotice',sync.last_error,'error');

  const findings=$('statsFindings');findings.innerHTML='';
  for(const text of data.automatic_findings||[]){const item=document.createElement('p');item.textContent=text;findings.appendChild(item);}
  if(!findings.children.length)findings.innerHTML='<p class="muted">Pas encore assez de données pour formuler une recommandation.</p>';

  const times=$('statsBestTimes');times.innerHTML='';
  const bestTimes=data.best_times||[];const maxRate=Math.max(...bestTimes.map(item=>Number(item.avg_engagement_rate)||0),1);
  for(const item of bestTimes){
    const row=document.createElement('div');row.className='stats-bar-row';row.innerHTML='<div class="stats-bar-label"><strong></strong><span></span></div><div class="stats-bar-track"><div></div></div>';
    row.querySelector('strong').textContent=`${item.weekday} • ${String(item.hour).padStart(2,'0')} h`;
    row.querySelector('span').textContent=`${Number(item.avg_engagement_rate||0).toFixed(1)} % • ${item.count} publication(s)`;
    row.querySelector('.stats-bar-track div').style.width=`${Math.max(4,Number(item.avg_engagement_rate||0)/maxRate*100)}%`;times.appendChild(row);
  }
  if(!times.children.length)times.innerHTML='<p class="muted">Aucun créneau comparable.</p>';

  analyticsPosts=Array.isArray(data.top_posts)?data.top_posts:[];
  renderAnalyticsPosts();
  renderAssistantReport(data.assistant_report,data.assistant_report_created_at);
}
async function loadAnalytics(){
  hideNotice('statsNotice');
  try{const data=await api('/api/analytics/dashboard');renderAnalytics(data);}
  catch(err){setNotice('statsNotice',err.message,'error');}
}
$('syncStatsBtn').addEventListener('click',async()=>{
  if(!confirm('Synchroniser maintenant les statistiques de tes publications depuis Meta ?'))return;
  const button=$('syncStatsBtn');button.disabled=true;button.textContent='Synchronisation…';hideNotice('statsNotice');
  try{
    const data=await api('/api/analytics/sync',{method:'POST'});
    await loadAnalytics();
    if(data.sync.permission_required)setNotice('statsNotice',data.sync.last_error,'error');
    else setNotice('statsNotice',`${data.sync.metrics_updated} publication(s) synchronisée(s).`,'success');
  }catch(err){setNotice('statsNotice',err.message,'error');}
  finally{button.disabled=false;button.textContent='Synchroniser avec Instagram';}
});
$('analyzeStatsBtn').addEventListener('click',async()=>{
  if(!confirm('Envoyer à Groq uniquement les chiffres agrégés et caractéristiques anonymisées des hooks pour générer ton analyse ?'))return;
  const button=$('analyzeStatsBtn');button.disabled=true;button.textContent='Analyse…';hideNotice('statsAssistantNotice');
  try{
    const data=await api('/api/analytics/assistant',{method:'POST'});
    renderAssistantReport(data.report,new Date().toISOString());
    setNotice('statsAssistantNotice',`Analyse terminée avec ${data.model}.`,'success');
  }catch(err){setNotice('statsAssistantNotice',err.message,'error');}
  finally{button.disabled=false;button.textContent='Analyser mes performances';}
});
$('statsSort').addEventListener('change',renderAnalyticsPosts);

function urlBase64ToUint8Array(base64String){const padding='='.repeat((4-base64String.length%4)%4);const base64=(base64String+padding).replace(/-/g,'+').replace(/_/g,'/');const raw=atob(base64);return Uint8Array.from([...raw].map(char=>char.charCodeAt(0)));}
function pushPreferences(){return {before_publication:$('notifyBefore').checked,published:$('notifyPublished').checked,failed:$('notifyFailed').checked,manual_music:$('notifyMusic').checked,studio_login:$('notifyLogin').checked,instagram_token:$('notifyToken').checked};}
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
updateDraftCount();configureMediaKind(false);updatePublicationOptions();refreshStudioSoundSetting();loadV2Status();
const requestedTab=new URLSearchParams(location.search).get('tab');if(requestedTab)activateTab(requestedTab);
