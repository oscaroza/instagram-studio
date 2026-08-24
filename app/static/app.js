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
let calendarView = (()=>{try{return localStorage.getItem('igstudio.calendarView')||'month';}catch{return 'month';}})();
if(!['month','week','list'].includes(calendarView))calendarView='month';
let selectedMediaItems = [];
let libraryItems = [];
let analyticsPosts = [];
let preparedInstagramShare = null;
const APPEARANCE_PRESETS = {
  studio:{accent:'#9f7aea',background:'#08090d',surface:'#11131a',text:'#f6f7fb',density:'comfortable',radius:18},
  ocean:{accent:'#28b8d8',background:'#06131d',surface:'#0d2331',text:'#f3fbff',density:'comfortable',radius:18},
  sunset:{accent:'#ff6b7a',background:'#160b12',surface:'#28131d',text:'#fff6f8',density:'comfortable',radius:20},
  graphite:{accent:'#d9dde7',background:'#090a0c',surface:'#181a1f',text:'#f5f6f8',density:'compact',radius:14}
};
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
function formatUploadDuration(seconds){
  if(!Number.isFinite(seconds)||seconds<0)return 'Calcul du temps restant…';
  if(seconds<1)return 'Moins d’une seconde restante';
  if(seconds<60)return `${Math.ceil(seconds)} s restantes`;
  const minutes=Math.floor(seconds/60),remaining=Math.ceil(seconds%60);
  return `${minutes} min ${String(remaining).padStart(2,'0')} s restantes`;
}
function hideUploadTransfer(){
  $('uploadTransferProgress').classList.add('hidden');
}
function showUploadTransfer({title,loaded,total,startedAt,processing=false}){
  const safeTotal=Math.max(1,total),safeLoaded=Math.min(safeTotal,Math.max(0,loaded));
  const percent=Math.min(100,(safeLoaded/safeTotal)*100);
  const elapsed=Math.max(.001,(performance.now()-startedAt)/1000);
  const speed=safeLoaded/elapsed;
  $('uploadTransferProgress').classList.remove('hidden');
  $('uploadTransferTitle').textContent=title;
  $('uploadTransferPercent').textContent=`${percent.toLocaleString('fr-FR',{minimumFractionDigits:1,maximumFractionDigits:1})} %`;
  $('uploadTransferBar').value=percent;
  $('uploadTransferBar').setAttribute('aria-valuetext',`${percent.toFixed(1)} pour cent`);
  $('uploadTransferBytes').textContent=`${formatBytes(safeLoaded)} / ${formatBytes(total)}${speed>0?` • ${formatBytes(speed)}/s`:''}`;
  $('uploadTransferEta').textContent=processing?'Envoi terminé • vérification du fichier…':formatUploadDuration(speed>0?(safeTotal-safeLoaded)/speed:Infinity);
}
function uploadMediaFile(file,onProgress){
  return new Promise((resolve,reject)=>{
    const request=new XMLHttpRequest();
    request.open('POST','/api/upload');
    request.responseType='json';
    request.timeout=10*60*1000;
    request.upload.addEventListener('progress',event=>{
      if(event.lengthComputable)onProgress(event.loaded,event.total);
    });
    request.addEventListener('load',()=>{
      if(request.status===401){expireStudioSession();reject(new Error('Session expirée. Actualise la page pour te reconnecter.'));return;}
      let data=request.response;
      if(!data){try{data=JSON.parse(request.responseText||'{}');}catch{reject(new Error(`Réponse serveur invalide (HTTP ${request.status}).`));return;}}
      if(request.status<200||request.status>=300||!data.ok){reject(new Error(data.error||`Échec de l’upload (HTTP ${request.status}).`));return;}
      resolve(data);
    });
    request.addEventListener('error',()=>reject(new Error('Connexion interrompue pendant l’upload.')));
    request.addEventListener('timeout',()=>reject(new Error('L’upload a pris trop de temps. Réessaie.')));
    const form=new FormData();form.append('file',file);request.send(form);
  });
}
async function api(url, options={}){
  const response=await fetch(url,options);
  if(response.status===401){expireStudioSession();throw new Error('Session expirée. Actualise la page pour te reconnecter.');}
  let data={}; try{data=await response.json();}catch{throw new Error(`Réponse serveur invalide (HTTP ${response.status}).`);}
  if(!data.ok) throw new Error(data.error||'Erreur serveur.');
  return data;
}

function appearanceFromForm(){
  return {
    accent:$('appearanceAccent').value,
    background:$('appearanceBackground').value,
    surface:$('appearanceSurface').value,
    text:$('appearanceText').value,
    density:$('appearanceDensity').value,
    radius:Number($('appearanceRadius').value)
  };
}
function matchingAppearancePreset(appearance){
  return Object.entries(APPEARANCE_PRESETS).find(([,preset])=>
    ['accent','background','surface','text','density','radius'].every(key=>String(preset[key])===String(appearance[key]))
  )?.[0]||'';
}
function appearanceAccentText(hexColor){
  const value=hexColor.replace('#','');
  const [red,green,blue]=[0,2,4].map(index=>parseInt(value.slice(index,index+2),16)/255);
  return .2126*red+.7152*green+.0722*blue>=.48?'#08090d':'#ffffff';
}
function applyAppearance(appearance,{syncForm=true}={}){
  if(!appearance)return;
  const style=document.body.style;
  style.setProperty('--accent',appearance.accent);
  style.setProperty('--accent2',appearance.accent);
  style.setProperty('--accent-contrast',appearance.accent_text||appearanceAccentText(appearance.accent));
  style.setProperty('--bg',appearance.background);
  style.setProperty('--surface',appearance.surface);
  style.setProperty('--text',appearance.text);
  style.setProperty('--card-radius',`${appearance.radius}px`);
  document.body.dataset.density=appearance.density;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content',appearance.background);
  if(syncForm){
    $('appearanceAccent').value=appearance.accent;
    $('appearanceBackground').value=appearance.background;
    $('appearanceSurface').value=appearance.surface;
    $('appearanceText').value=appearance.text;
    $('appearanceDensity').value=appearance.density;
    $('appearanceRadius').value=appearance.radius;
  }
  $('appearanceRadiusValue').textContent=`${appearance.radius} px`;
  const activePreset=matchingAppearancePreset(appearance);
  document.querySelectorAll('[data-appearance-preset]').forEach(button=>button.classList.toggle('active',button.dataset.appearancePreset===activePreset));
}
async function loadAppearance(){
  hideNotice('appearanceNotice');
  try{
    const data=await api('/api/preferences/appearance');
    applyAppearance(data.appearance);
  }catch(err){setNotice('appearanceNotice',err.message,'error');}
}

function activateTab(name){
  const tab=document.querySelector(`[data-tab="${name}"]`);
  const panel=$(name); if(!tab||!panel)return;
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  tab.classList.add('active');panel.classList.add('active');
  if(window.matchMedia('(max-width:760px)').matches){tab.scrollIntoView({behavior:'smooth',block:'nearest',inline:'center'});}
  history.replaceState({},'',`?tab=${name}`);
  if(name==='drafts')renderDrafts();
  if(name==='settings'){loadPublishingLimit();loadInstagramTokenHealth();loadLoginHistory();loadPasskeys();loadR2Usage();}
  if(name==='customize')loadAppearance();
  if(name==='library')loadLibrary();
  if(name==='calendar')loadCalendar();
  if(name==='stats'){loadAnalytics();loadAssistantChat();resumeAnalyticsSyncProgress();}
  if(name==='notifications')refreshPushState();
}
for(const tab of document.querySelectorAll('.tab')){
  tab.addEventListener('click',()=>activateTab(tab.dataset.tab));
}

for(const button of document.querySelectorAll('[data-appearance-preset]')){
  button.addEventListener('click',()=>applyAppearance(APPEARANCE_PRESETS[button.dataset.appearancePreset]));
}
for(const id of ['appearanceAccent','appearanceBackground','appearanceSurface','appearanceText','appearanceDensity','appearanceRadius']){
  $(id).addEventListener('input',()=>applyAppearance(appearanceFromForm(),{syncForm:false}));
  $(id).addEventListener('change',()=>applyAppearance(appearanceFromForm(),{syncForm:false}));
}
$('saveAppearanceBtn').addEventListener('click',async()=>{
  const button=$('saveAppearanceBtn');button.disabled=true;button.textContent='Enregistrement…';hideNotice('appearanceNotice');
  try{
    const data=await api('/api/preferences/appearance',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(appearanceFromForm())});
    applyAppearance(data.appearance);setNotice('appearanceNotice','Thème enregistré dans MongoDB. Il sera appliqué sur tous tes appareils.','success');
  }catch(err){setNotice('appearanceNotice',err.message,'error');}
  finally{button.disabled=false;button.textContent='Enregistrer sur tous mes appareils';}
});
$('resetAppearanceBtn').addEventListener('click',async()=>{
  if(!confirm('Revenir au thème Studio violet sur tous tes appareils ?'))return;
  const button=$('resetAppearanceBtn');button.disabled=true;hideNotice('appearanceNotice');
  try{
    const data=await api('/api/preferences/appearance',{method:'DELETE'});applyAppearance(data.appearance);setNotice('appearanceNotice','Thème d’origine restauré.','success');
  }catch(err){setNotice('appearanceNotice',err.message,'error');}
  finally{button.disabled=false;}
});

function syncMediaFields(){
  $('mediaItemsJson').value=JSON.stringify(selectedMediaItems);
  const first=selectedMediaItems[0]||{};
  $('videoUrl').value=selectedMediaItems.length===1?(first.url||''):'';
  $('libraryId').value=selectedMediaItems.length===1?(first.library_id||''):'';
  $('thumbnailUrl').value=first.thumbnail_url||'';
}
function isCarouselMode(kind=$('mediaKind').value){
  return kind==='carousel'||kind==='carousel_video';
}
function renderSelectedMedia(){
  const root=$('selectedMedia');root.innerHTML='';
  const reorderable=isCarouselMode()&&selectedMediaItems.length>1;
  $('carouselOrderHelp').classList.toggle('hidden',!reorderable);
  selectedMediaItems.forEach((item,index)=>{
    const row=document.createElement('div');row.className='selected-media-item';row.dataset.index=index;
    const previewTag=item.media_type==='video'?'video':'img';
    row.innerHTML=`<span class="media-drag-handle" role="button" tabindex="0" aria-label="Déplacer ce média">⋮⋮</span><${previewTag} aria-label="Aperçu du média"></${previewTag}><div><strong></strong><span></span></div><button class="ghost remove-media" type="button">Retirer</button>`;
    const preview=row.querySelector(previewTag);preview.src=item.thumbnail_url||item.url||'/static/icons/icon-192.png';
    if(previewTag==='video'){preview.muted=true;preview.playsInline=true;preview.preload='metadata';}
    row.querySelector('strong').textContent=item.name||`Média ${index+1}`;
    row.querySelector('div span').textContent=`Position ${index+1} • ${item.media_type==='image'?'Photo JPEG':'Vidéo'}${item.size?` • ${formatBytes(item.size)}`:''}`;
    const handle=row.querySelector('.media-drag-handle');handle.classList.toggle('hidden',!reorderable);
    handle.addEventListener('keydown',(event)=>{
      if(!reorderable||!['ArrowUp','ArrowDown'].includes(event.key))return;
      event.preventDefault();const target=index+(event.key==='ArrowUp'?-1:1);reorderSelectedMedia(index,target);
      root.children[Math.max(0,Math.min(target,root.children.length-1))]?.querySelector('.media-drag-handle')?.focus();
    });
    handle.addEventListener('pointerdown',(event)=>startMediaPointerDrag(event,row,root));
    row.querySelector('.remove-media').onclick=()=>{selectedMediaItems.splice(index,1);syncMediaFields();renderSelectedMedia();};
    root.appendChild(row);
  });
}
function reorderSelectedMedia(fromIndex,toIndex){
  if(fromIndex===toIndex||toIndex<0||toIndex>=selectedMediaItems.length)return;
  const [moved]=selectedMediaItems.splice(fromIndex,1);selectedMediaItems.splice(toIndex,0,moved);syncMediaFields();renderSelectedMedia();
}
function startMediaPointerDrag(event,row,root){
  if(!isCarouselMode()||selectedMediaItems.length<2)return;
  event.preventDefault();const handle=event.currentTarget;handle.setPointerCapture?.(event.pointerId);row.classList.add('dragging');
  const finish=()=>{
    const reordered=[...root.children].map(element=>selectedMediaItems[Number(element.dataset.index)]).filter(Boolean);
    if(reordered.length===selectedMediaItems.length)selectedMediaItems=reordered;
    syncMediaFields();renderSelectedMedia();
  };
  handle.addEventListener('pointermove',(moveEvent)=>{
    const target=document.elementFromPoint(moveEvent.clientX,moveEvent.clientY)?.closest('.selected-media-item');
    if(!target||target===row||target.parentElement!==root)return;
    const bounds=target.getBoundingClientRect();
    root.insertBefore(row,moveEvent.clientY<bounds.top+bounds.height/2?target:target.nextSibling);
  });
  handle.addEventListener('pointerup',finish,{once:true});handle.addEventListener('pointercancel',finish,{once:true});
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
  clearPreparedInstagramShare();selectedMediaItems=[];$('videoFile').value='';$('videoUrl').value='';$('libraryId').value='';$('thumbnailUrl').value='';syncMediaFields();renderSelectedMedia();hideUploadTransfer();hideNotice('uploadProgress');
}
function configureMediaKind(clear=true){
  const kind=$('mediaKind').value,file=$('videoFile');
  if(clear)clearMediaSelection();
  if(kind==='reel'){
    file.accept='video/mp4,video/quicktime,video/x-m4v';file.multiple=false;
    $('mediaUploadLabel').textContent='Choisir une vidéo';$('mediaUploadHelp').textContent='MP4 / MOV / M4V';$('mediaUrlLabel').textContent='URL vidéo publique';
  }else if(kind==='carousel_video'){
    file.accept='video/mp4,video/quicktime,video/x-m4v';file.multiple=true;
    $('mediaUploadLabel').textContent='Choisir 2 à 10 vidéos';$('mediaUploadHelp').textContent=`MP4 / MOV / M4V • max ${document.body.dataset.maxUploadMb||250} Mo par vidéo`;$('mediaUrlLabel').textContent='URL vidéo publique';
  }else{
    file.accept='image/jpeg';file.multiple=isCarouselMode(kind);
    $('mediaUploadLabel').textContent=kind==='carousel'?'Choisir 2 à 10 photos':'Choisir une photo';$('mediaUploadHelp').textContent='JPG / JPEG • 8 Mo max par photo';$('mediaUrlLabel').textContent='URL photo JPEG publique';
  }
  $('publicUrlBlock').classList.toggle('hidden',isCarouselMode(kind));
  $('publicationModeField').classList.toggle('hidden',kind!=='reel');
  $('muteOption').classList.toggle('hidden',kind!=='reel');
  if(kind!=='reel'){$('publicationMode').value='normal';$('muteAudio').checked=false;}
  $('carouselOrderHelp').classList.toggle('hidden',!isCarouselMode(kind)||selectedMediaItems.length<2);
  updatePublicationOptions();
}
$('mediaKind').addEventListener('change',()=>configureMediaKind(true));
$('videoFile').addEventListener('change',async(e)=>{
  const fileInput=e.currentTarget,files=[...fileInput.files];if(!files.length)return;
  const kind=$('mediaKind').value,expected=kind==='reel'||kind==='carousel_video'?'video':'image';
  if(isCarouselMode(kind)&&selectedMediaItems.length+files.length>10){setNotice('uploadProgress','Un carrousel contient au maximum 10 médias.','error');fileInput.value='';return;}
  if(!isCarouselMode(kind))selectedMediaItems=[];
  const totalBytes=files.reduce((total,file)=>total+file.size,0);
  let completedBytes=0;
  const startedAt=performance.now();
  const uploadZone=fileInput.closest('.upload-zone');
  fileInput.disabled=true;uploadZone?.classList.add('uploading');
  hideNotice('uploadProgress');
  try{
    for(const [index,file] of files.entries()){
      const title=files.length>1?`Envoi ${index+1}/${files.length} • ${file.name}`:`Envoi de ${file.name}`;
      showUploadTransfer({title,loaded:completedBytes,total:totalBytes,startedAt});
      const data=await uploadMediaFile(file,(loaded,requestTotal)=>{
        const fileLoaded=requestTotal>0?Math.min(file.size,(loaded/requestTotal)*file.size):Math.min(file.size,loaded);
        showUploadTransfer({title,loaded:completedBytes+fileLoaded,total:totalBytes,startedAt,processing:requestTotal>0&&loaded>=requestTotal});
      });
      completedBytes+=file.size;
      showUploadTransfer({title,loaded:completedBytes,total:totalBytes,startedAt,processing:true});
      if(data.media_type!==expected)throw new Error('Le fichier ne correspond pas au type de publication choisi.');
      selectedMediaItems.push({url:data.url,library_id:'',thumbnail_url:data.media_type==='image'?data.url:'',media_type:data.media_type,name:file.name,size:data.size});
      syncMediaFields();renderSelectedMedia();
    }
    syncMediaFields();renderSelectedMedia();
    const label=isCarouselMode(kind)?`${selectedMediaItems.length} ${kind==='carousel_video'?'vidéos':'photos'} prêtes`:`${kind==='photo'?'Photo':'Vidéo'} prête`;
    hideUploadTransfer();
    setNotice('uploadProgress',`${label} avec une URL publique temporaire.`,'success');
  }catch(err){hideUploadTransfer();setNotice('uploadProgress',err.message,'error');}
  finally{fileInput.disabled=false;uploadZone?.classList.remove('uploading');fileInput.value='';}
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
  const typeLabel={reel:'le Reel',photo:'la photo',carousel:'le carrousel photo',carousel_video:'le carrousel vidéo'}[kind];
  $('scheduleFields').classList.toggle('hidden',!scheduled);
  $('musicHelp').classList.toggle('hidden',!music);
  $('publishBtn').textContent=scheduled?`Programmer ${typeLabel}`:music?'Finaliser dans Instagram':`Publier ${typeLabel}`;
  $('publishBtn').className=scheduled?'primary':music?'secondary':'danger';
}
$('scheduleEnabled').addEventListener('change',updatePublicationOptions);
$('musicEnabled').addEventListener('change',updatePublicationOptions);

function publicationPayload(){
  const fullCaption=[$('hook').value.trim(),$('caption').value.trim(),$('hashtags').value.trim()].filter(Boolean).join('\n\n');
  const selectedKind=$('mediaKind').value,mediaKind=selectedKind==='carousel_video'?'carousel':selectedKind;
  let items=selectedMediaItems.map(item=>({...item}));
  const publicUrl=$('videoUrl').value.trim();
  if(!isCarouselMode(selectedKind)&&publicUrl&&(!items.length||items[0].url!==publicUrl)){
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
  if(payload.media_kind==='carousel'&&(payload.media_items.length<2||payload.media_items.length>10)){setNotice('actionMessage','Ajoute entre 2 et 10 médias au carrousel.','error');return;}
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
        setNotice('actionMessage',`Copie durable vers le stockage média (${index+1}/${payload.media_items.length})…`);
        const promoted=await api('/api/library/promote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_url:item.url,media_type:item.media_type,mute_audio:payload.mute_audio,description:payload.title})});
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
  const root=$('loginHistory'),devicesRoot=$('loginDevices');
  root.innerHTML='<p class="muted">Chargement…</p>';devicesRoot.innerHTML='';hideNotice('loginHistoryNotice');
  try{
    const data=await api('/api/security/login-history');root.innerHTML='';devicesRoot.innerHTML='';
    const blocked=(data.devices||[]).filter(device=>device.blocked).length;
    $('blockedDevicesSummary').innerHTML='<strong></strong><span></span>';
    $('blockedDevicesSummary').querySelector('strong').textContent=`${blocked} appareil(s) bloqué(s)`;
    $('blockedDevicesSummary').querySelector('span').textContent=`${(data.devices||[]).length} appareil(s) ou navigateur(s) reconnus`;
    if(!(data.devices||[]).length)devicesRoot.innerHTML='<p class="muted">Aucun appareil enregistré pour le moment.</p>';
    for(const device of data.devices||[]){
      const card=document.createElement('article');card.className=`security-device${device.blocked?' blocked':''}`;
      card.innerHTML='<div class="security-device-main"><div class="row-between"><strong class="name"></strong><span class="pill status"></span></div><p class="last-seen"></p></div><div class="security-device-actions"></div>';
      card.querySelector('.name').textContent=`${device.device} • ${device.browser}`;
      const status=card.querySelector('.status');
      if(!device.manageable){status.textContent='Historique ancien';status.classList.add('warn');}
      else if(device.current){status.textContent='Cet appareil';status.classList.add('ok');}
      else if(device.block_type==='manual'){status.textContent='Bloqué manuellement';status.classList.add('warn');}
      else if(device.block_type==='temporary'){status.textContent='Bloqué après 5 essais';status.classList.add('warn');}
      else{status.textContent='Autorisé';status.classList.add('ok');}
      let details=`Dernière activité : ${new Date(device.last_seen_at).toLocaleString('fr-FR',{dateStyle:'medium',timeStyle:'short'})}`;
      if(device.locked_until)details+=` • déblocage automatique ${new Date(device.locked_until).toLocaleString('fr-FR',{dateStyle:'medium',timeStyle:'short'})}`;
      card.querySelector('.last-seen').textContent=details;
      if(device.manageable&&!device.current){
        const button=document.createElement('button');button.type='button';button.className=device.blocked?'secondary':'danger';button.textContent=device.blocked?'Débloquer':'Bloquer cet appareil';
        button.onclick=()=>changeDeviceAccess(device,!device.blocked);card.querySelector('.security-device-actions').appendChild(button);
      }
      devicesRoot.appendChild(card);
    }
    if(!data.events.length){root.innerHTML='<p class="muted">Aucune connexion enregistrée pour le moment.</p>';return;}
    for(const event of data.events){
      const row=document.createElement('div');row.className='draft-item';
      const date=new Date(event.created_at).toLocaleString('fr-FR',{dateStyle:'medium',timeStyle:'short'});
      row.innerHTML='<div class="row-between"><strong></strong><span class="pill"></span></div><p></p>';
      row.querySelector('strong').textContent=`${event.device} • ${event.browser}`;
      const status=row.querySelector('.pill');status.textContent=event.blocked?'Appareil bloqué':event.success?'Connexion réussie':'Essai refusé';status.classList.add(event.blocked||!event.success?'warn':'ok');
      row.querySelector('p').textContent=date;root.appendChild(row);
    }
  }catch(err){root.innerHTML='';devicesRoot.innerHTML='';$('blockedDevicesSummary').textContent='Sécurité indisponible';setNotice('loginHistoryNotice',err.message,'error');}
}
async function changeDeviceAccess(device,block){
  const action=block?'bloquer':'débloquer';
  if(!confirm(`${block?'Bloquer':'Débloquer'} ${device.device} • ${device.browser} ?${block?' Même avec le bon code, ce navigateur ne pourra plus entrer dans le Studio.':''}`))return;
  try{await api(`/api/security/devices/${encodeURIComponent(device.device_key)}/${block?'block':'unblock'}`,{method:'POST'});setNotice('loginHistoryNotice',`Appareil ${action === 'bloquer'?'bloqué':'débloqué'} ✅`,'success');await loadLoginHistory();}
  catch(err){setNotice('loginHistoryNotice',err.message,'error');}
}
$('refreshLoginHistory').addEventListener('click',loadLoginHistory);
$('studioSoundEnabled').addEventListener('change',(event)=>{
  setStudioSoundEnabled(event.target.checked);refreshStudioSoundSetting();
  if(event.target.checked)playStudioChime();
});
$('testStudioSoundBtn').addEventListener('click',playStudioChime);

async function loadPasskeys(){
  const root=$('passkeyList');root.innerHTML='<p class="muted">Chargement…</p>';hideNotice('passkeyNotice');
  const supported=Boolean(window.Passkeys?.supported());$('registerPasskeyBtn').disabled=!supported;
  if(!supported){root.innerHTML='<p class="muted">Les passkeys ne sont pas prises en charge sur ce navigateur.</p>';return;}
  try{
    const data=await api('/api/passkeys');root.innerHTML='';
    if(!data.items.length)root.innerHTML='<p class="muted">Aucune passkey configurée. Ajoute d’abord Face ID depuis cet appareil.</p>';
    for(const item of data.items){
      const card=document.createElement('article');card.className='passkey-item';card.innerHTML='<div><strong class="label"></strong><p class="details"></p></div><button class="danger" type="button">Supprimer</button>';
      card.querySelector('.label').textContent=item.label;
      const created=item.created_at?new Date(item.created_at).toLocaleDateString('fr-FR'):'date inconnue';
      const used=item.last_used_at?` • dernière utilisation ${new Date(item.last_used_at).toLocaleString('fr-FR',{dateStyle:'medium',timeStyle:'short'})}`:'';
      card.querySelector('.details').textContent=`Ajoutée le ${created}${used}${item.backed_up?' • synchronisée':''}`;
      card.querySelector('button').onclick=()=>removePasskey(item);root.appendChild(card);
    }
  }catch(err){root.innerHTML='';setNotice('passkeyNotice',err.message,'error');}
}
async function removePasskey(item){
  if(!confirm(`Supprimer « ${item.label} » ? Le code d’accès restera disponible.`))return;
  try{await api(`/api/passkeys/${encodeURIComponent(item.id)}`,{method:'DELETE'});setNotice('passkeyNotice','Passkey supprimée.','success');await loadPasskeys();}
  catch(err){setNotice('passkeyNotice',err.message,'error');}
}
$('registerPasskeyBtn').addEventListener('click',async()=>{
  const button=$('registerPasskeyBtn');button.disabled=true;button.textContent='Préparation Face ID…';hideNotice('passkeyNotice');
  try{
    if(!window.Passkeys?.supported())throw new Error('Les passkeys ne sont pas prises en charge sur ce navigateur.');
    const options=await api('/api/passkeys/register/options',{method:'POST'});
    const credential=await window.Passkeys.create(options.public_key);
    await api('/api/passkeys/register/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ceremony_id:options.ceremony_id,credential,label:'Face ID / passkey'})});
    setNotice('passkeyNotice','Face ID / passkey est maintenant prêt pour les prochaines connexions.','success');await loadPasskeys();
  }catch(err){setNotice('passkeyNotice',err?.name==='NotAllowedError'?'La création de la passkey a été annulée ou a expiré.':err.message,'error');}
  finally{button.disabled=false;button.textContent='Ajouter Face ID / passkey';}
});

async function loadV2Status(){
  const pill=$('mongoStatusPill'),text=$('mongoStatusText'),storagePill=$('mediaStorageStatusPill'),storageText=$('mediaStorageStatusText');
  try{
    const data=await api('/api/v2/status');
    if(data.mongodb_ready){pill.textContent='MongoDB ✓';pill.className='pill ok';text.textContent='Connecté';text.className='cap-on';}
    else if(data.mongodb_configured){pill.textContent='MongoDB connexion impossible';pill.className='pill warn';text.textContent='Connexion Atlas impossible';text.className='cap-off';}
    else{pill.textContent='MongoDB à configurer';pill.className='pill warn';text.textContent='MONGODB_URI manquante';text.className='cap-off';}
    const storageLabel=data.media_storage_label||'Stockage média';
    if(data.media_storage_ready){
      storagePill.textContent=`${storageLabel} ✓`;storagePill.className='pill ok';storageText.className='cap-on';
      const storageFormatter=data.media_storage_provider==='r2'?formatR2Storage:formatBytes;
      storageText.textContent=data.media_storage_limit_bytes?`Connecté • ${storageFormatter(data.media_storage_usage_bytes)} / ${storageFormatter(data.media_storage_limit_bytes)} protégés`:'Connecté';
    }
    else if(data.media_storage_configured){storagePill.textContent=`${storageLabel} refusé`;storagePill.className='pill warn';storageText.textContent=data.media_storage_error||'Connexion impossible';storageText.className='cap-off';}
    else{storagePill.textContent=`${storageLabel} à configurer`;storagePill.className='pill warn';storageText.textContent='Variables de stockage manquantes';storageText.className='cap-off';}
  }catch(err){
    pill.textContent='MongoDB indisponible';pill.className='pill warn';text.textContent=err.message;text.className='cap-off';
    storagePill.textContent='Stockage indisponible';storagePill.className='pill warn';storageText.textContent=err.message;storageText.className='cap-off';
  }
}

function renderR2Quota(prefix,used,limit,formatter){
  const safeUsed=Math.max(0,Number(used)||0),safeLimit=Math.max(1,Number(limit)||1),percent=safeUsed/safeLimit*100;
  const bar=$(prefix+'Bar'),track=bar.parentElement;
  bar.style.width=`${Math.min(100,percent)}%`;
  track.classList.toggle('warn',percent>=75&&percent<90);track.classList.toggle('danger',percent>=90);
  $(prefix+'Percent').textContent=`${percent.toLocaleString('fr-FR',{maximumFractionDigits:1})} %`;
  $(prefix+'Value').textContent=`${formatter(safeUsed)} / ${formatter(safeLimit)}`;
  return Math.max(0,safeLimit-safeUsed);
}
function formatR2Storage(value){
  const bytes=Math.max(0,Number(value)||0);
  if(bytes<1_000_000_000)return `${(bytes/1_000_000).toLocaleString('fr-FR',{maximumFractionDigits:1})} Mo`;
  return `${(bytes/1_000_000_000).toLocaleString('fr-FR',{maximumFractionDigits:2})} Go`;
}
async function loadR2Usage(){
  const button=$('refreshR2UsageBtn');button.disabled=true;button.textContent='Actualisation…';hideNotice('r2UsageNotice');
  try{
    const data=await api('/api/r2/usage'),usage=data.usage||{};
    const alerts=[];
    const accountStorage=usage.account_storage_bytes;
    const displayedStorage=accountStorage===null||accountStorage===undefined?usage.bucket_storage_bytes:accountStorage;
    const storageRemaining=renderR2Quota('r2Storage',displayedStorage,usage.free_storage_bytes||10000000000,formatR2Storage);
    $('r2StorageRemaining').textContent=`${formatR2Storage(storageRemaining)} avant 10 Go • uploads bloqués à ${formatR2Storage(usage.studio_storage_limit_bytes||9000000000)}`;
    if(Number(displayedStorage)/(Number(usage.free_storage_bytes)||10000000000)>=.8)alerts.push('Le stockage R2 dépasse 80 % du quota gratuit.');
    if(usage.analytics_ready){
      const remainingA=renderR2Quota('r2ClassA',usage.class_a?.used,usage.class_a?.limit||1000000,formatStat);
      const remainingB=renderR2Quota('r2ClassB',usage.class_b?.used,usage.class_b?.limit||10000000,formatStat);
      $('r2ClassARemaining').textContent=`${formatStat(remainingA)} restantes • écritures et listes`;
      $('r2ClassBRemaining').textContent=`${formatStat(remainingB)} restantes • lectures`;
      if(Number(usage.class_a?.percent)>=80)alerts.push('Les opérations de classe A dépassent 80 % du quota gratuit.');
      if(Number(usage.class_b?.percent)>=80)alerts.push('Les opérations de classe B dépassent 80 % du quota gratuit.');
      const unknown=Number(usage.unknown_operations)||0;
      if(unknown)alerts.push(`${formatStat(unknown)} opération(s) Cloudflare n’ont pas pu être classées automatiquement.`);
    }else{
      renderR2Quota('r2ClassA',0,1000000,formatStat);renderR2Quota('r2ClassB',0,10000000,formatStat);
      $('r2ClassAValue').textContent='Token Analytics manquant';$('r2ClassBValue').textContent='Token Analytics manquant';
      $('r2ClassARemaining').textContent='Ajoute CLOUDFLARE_ANALYTICS_API_TOKEN';$('r2ClassBRemaining').textContent='Permission Account Analytics — Read';
      setNotice('r2UsageNotice',usage.analytics_error||'Ajoute CLOUDFLARE_ANALYTICS_API_TOKEN dans Render pour afficher les opérations mensuelles. Le token doit uniquement avoir Account Analytics — Read.','error');
    }
    if(usage.bucket_storage_error)alerts.push(usage.bucket_storage_error);
    if(usage.analytics_ready&&alerts.length)setNotice('r2UsageNotice',`${alerts.join('\n')} Vérifie le dashboard Cloudflare avant d’approcher les limites.`,'error');
    const start=statsDate(usage.period_start);
    $('r2UsageMeta').textContent=`Mois en cours depuis le ${start}${usage.bucket_name?` • Bucket Studio « ${usage.bucket_name} » : ${formatR2Storage(usage.bucket_storage_bytes)}`:''} • stockage affiché : ${accountStorage===null||accountStorage===undefined?'mesure du bucket':'dernier relevé du compte Cloudflare'}.`;
  }catch(err){setNotice('r2UsageNotice',err.message,'error');$('r2UsageMeta').textContent='Statistiques Cloudflare indisponibles.';}
  finally{button.disabled=false;button.textContent='Actualiser Cloudflare';}
}
$('refreshR2UsageBtn').addEventListener('click',loadR2Usage);

async function loadLibrary(){
  const root=$('libraryGrid');root.innerHTML='<p class="muted">Chargement…</p>';hideNotice('libraryNotice');
  try{
    const data=await api('/api/library');libraryItems=data.items||[];
    const providers=Object.entries(data.providers||{}).map(([provider,value])=>`${value.count} sur ${provider==='r2'?'R2':'Cloudinary'}`).join(' • ');
    $('libraryUsage').textContent=`${libraryItems.length} média(s) • ${formatBytes(data.total_bytes)}${providers?` • ${providers}`:''}`;
    renderLibrary();
  }catch(err){root.innerHTML='';setNotice('libraryNotice',err.message,'error');}
}
function filteredLibraryItems(){
  const search=$('librarySearch').value.trim().toLocaleLowerCase('fr');
  const type=$('libraryTypeFilter').value,dateFilter=$('libraryDateFilter').value,weight=$('libraryWeightFilter').value,usage=$('libraryUsageFilter').value;
  const now=Date.now(),day=86400000;
  return libraryItems.filter(item=>{
    const itemType=item.media_type||item.resource_type||'video';
    if(type!=='all'&&itemType!==type)return false;
    const haystack=`${item.original_filename||''} ${item.description||''}`.toLocaleLowerCase('fr');
    if(search&&!haystack.includes(search))return false;
    const age=now-Date.parse(item.created_at||0);
    if(dateFilter==='7'&&age>7*day)return false;
    if(dateFilter==='30'&&age>30*day)return false;
    if(dateFilter==='older'&&age<=30*day)return false;
    const bytes=Number(item.bytes)||0,tenMb=10*1024*1024,fiftyMb=50*1024*1024;
    if(weight==='small'&&bytes>=tenMb)return false;
    if(weight==='medium'&&(bytes<tenMb||bytes>fiftyMb))return false;
    if(weight==='large'&&bytes<=fiftyMb)return false;
    if(usage==='active'&&Number(item.active_usage_count||0)<1)return false;
    if(usage==='used'&&Number(item.usage_count||0)<1)return false;
    if(usage==='unused'&&Number(item.usage_count||0)>0)return false;
    return true;
  });
}
function renderLibrary(){
  const root=$('libraryGrid');root.innerHTML='';const items=filteredLibraryItems();
  $('libraryResults').textContent=`${items.length} résultat(s) affiché(s)`;
  if(!libraryItems.length){root.innerHTML='<p class="muted">La bibliothèque est vide.</p>';return;}
  if(!items.length){root.innerHTML='<p class="muted">Aucun média ne correspond à ces filtres.</p>';return;}
  for(const item of items){
      const card=document.createElement('article');card.className='media-card';
      card.innerHTML='<img alt=""><div class="media-card-body"><strong></strong><span class="muted small description"></span><span class="muted small meta"></span><span class="muted small usage"></span><div class="draft-actions"><button class="secondary use">Utiliser</button><button class="ghost delete">Supprimer</button></div></div>';
      card.querySelector('img').src=item.thumbnail_url||'/static/icons/icon-192.png';
      const itemType=item.media_type||item.resource_type||'video';
      card.querySelector('strong').textContent=item.original_filename||(itemType==='image'?'Photo':'Vidéo');
      const created=item.created_at?new Date(item.created_at).toLocaleDateString('fr-FR'):'Date inconnue';
      card.querySelector('.description').textContent=item.description||'Sans description';
      const provider=(item.storage_provider||'cloudinary')==='r2'?'R2':'Cloudinary';
      card.querySelector('.meta').textContent=itemType==='image'?`${formatBytes(item.bytes)} • ${item.width||'?'} × ${item.height||'?'} • ${provider} • ${created}`:`${formatBytes(item.bytes)} • ${Math.round(item.duration||0)} s • ${provider} • ${created}`;
      const uses=Number(item.usage_count||0),active=Number(item.active_usage_count||0);
      card.querySelector('.usage').textContent=active?`Utilisé dans ${active} programmation(s) active(s)`:uses?`Utilisé ${uses} fois`:'Jamais utilisé';
      card.querySelector('.use').onclick=()=>{
        const mediaItem={url:item.secure_url,library_id:item.id,thumbnail_url:item.thumbnail_url||'',media_type:itemType,name:item.original_filename||'Média',size:item.bytes||0};
        const selectedKind=$('mediaKind').value;
        const matchesCarousel=(itemType==='image'&&selectedKind==='carousel')||(itemType==='video'&&selectedKind==='carousel_video');
        if(matchesCarousel){
          if(selectedMediaItems.length>=10){setNotice('libraryNotice','Le carrousel contient déjà 10 médias.','error');return;}
          selectedMediaItems.push(mediaItem);
        }else{
          $('mediaKind').value=itemType==='image'?'photo':'reel';configureMediaKind(true);selectedMediaItems=[mediaItem];
        }
        syncMediaFields();renderSelectedMedia();activateTab('composer');setNotice('uploadProgress',`${itemType==='image'?'Photo':'Vidéo'} chargée depuis la bibliothèque.`,'success');
      };
      card.querySelector('.delete').onclick=async()=>{if(!confirm(`Supprimer définitivement « ${item.original_filename||'ce média'} » ?`))return;try{await api(`/api/library/${item.id}`,{method:'DELETE'});loadLibrary();}catch(err){setNotice('libraryNotice',err.message,'error');}};
      root.appendChild(card);
  }
}
$('refreshLibrary').addEventListener('click',loadLibrary);
for(const id of ['librarySearch','libraryTypeFilter','libraryDateFilter','libraryWeightFilter','libraryUsageFilter']){
  $(id).addEventListener(id==='librarySearch'?'input':'change',renderLibrary);
}

function startOfWeek(value){const date=new Date(value);date.setHours(0,0,0,0);date.setDate(date.getDate()-((date.getDay()+6)%7));return date;}
function calendarBounds(){
  if(calendarView==='week'){const start=startOfWeek(calendarCursor);const end=new Date(start);end.setDate(end.getDate()+7);return {start,end};}
  const start=new Date(calendarCursor.getFullYear(),calendarCursor.getMonth(),1);const end=new Date(calendarCursor.getFullYear(),calendarCursor.getMonth()+1,1);return {start,end};
}
function eventDate(item){return new Date(item.scheduled_for||item.published_at||item.created_at);}
function localDateKey(date){return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;}
function calendarTitle(start,end){
  if(calendarView==='week'){
    const last=new Date(end);last.setDate(last.getDate()-1);
    return `${start.toLocaleDateString('fr-FR',{day:'numeric',month:'short'})} – ${last.toLocaleDateString('fr-FR',{day:'numeric',month:'short',year:'numeric'})}`;
  }
  return new Intl.DateTimeFormat('fr-FR',{month:'long',year:'numeric'}).format(start);
}
async function loadCalendar(){
  const {start,end}=calendarBounds();$('calendarTitle').textContent=calendarTitle(start,end);hideNotice('calendarNotice');
  document.querySelectorAll('[data-calendar-view]').forEach(button=>{const active=button.dataset.calendarView===calendarView;button.className=active?'secondary active':'ghost';});
  const listOnly=calendarView==='list';document.querySelector('.calendar-weekdays').classList.toggle('hidden',listOnly);$('calendarGrid').classList.toggle('hidden',listOnly);$('calendarDragHelp').classList.toggle('hidden',listOnly);
  $('calendarListTitle').textContent=calendarView==='week'?'Publications de la semaine':calendarView==='list'?'Publications du mois • liste':'Publications du mois';
  try{const data=await api(`/api/publications/calendar?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`);renderCalendar(data.items,start,end);}
  catch(err){setNotice('calendarNotice',err.message,'error');$('calendarGrid').innerHTML='';$('calendarList').innerHTML='';}
}
function sameLocalDay(first,second){return first.getFullYear()===second.getFullYear()&&first.getMonth()===second.getMonth()&&first.getDate()===second.getDate();}
function calendarDayCell(date,items){
  const cell=document.createElement('div');cell.className='calendar-day';cell.dataset.date=localDateKey(date);cell.innerHTML='<span class="day-number"></span><div class="day-events"></div>';
  cell.querySelector('.day-number').textContent=calendarView==='week'?date.toLocaleDateString('fr-FR',{weekday:'short',day:'numeric'}):date.getDate();
  if(sameLocalDay(date,new Date()))cell.classList.add('today');
  installCalendarDropTarget(cell);
  for(const item of items.filter(value=>sameLocalDay(eventDate(value),date))){
    const badge=document.createElement('button');badge.className=`calendar-event status-${item.status}`;badge.textContent=item.title||statusLabels[item.status];badge.title=`${statusLabels[item.status]||item.status} • ${eventDate(item).toLocaleString()}`;
    badge.onclick=()=>{if(badge.dataset.justDragged)return;document.getElementById(`publication-${item.id}`)?.scrollIntoView({behavior:'smooth'});};
    if(item.status==='scheduled')installCalendarEventDrag(badge,item);
    cell.querySelector('.day-events').appendChild(badge);
  }
  return cell;
}
let draggedCalendarItem=null;
function installCalendarDropTarget(cell){
  cell.addEventListener('dragover',(event)=>{if(!draggedCalendarItem)return;event.preventDefault();cell.classList.add('drop-target');});
  cell.addEventListener('dragleave',(event)=>{if(!cell.contains(event.relatedTarget))cell.classList.remove('drop-target');});
  cell.addEventListener('drop',async(event)=>{event.preventDefault();cell.classList.remove('drop-target');if(draggedCalendarItem)await moveScheduledPublication(draggedCalendarItem,cell.dataset.date);});
}
function installCalendarEventDrag(badge,item){
  badge.draggable=true;badge.classList.add('draggable');
  badge.addEventListener('dragstart',(event)=>{draggedCalendarItem=item;badge.classList.add('dragging');event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',item.id);});
  badge.addEventListener('dragend',()=>{draggedCalendarItem=null;badge.classList.remove('dragging');document.querySelectorAll('.calendar-day.drop-target').forEach(cell=>cell.classList.remove('drop-target'));});
  badge.addEventListener('pointerdown',(event)=>{
    if(event.pointerType==='mouse')return;
    const startX=event.clientX,startY=event.clientY;let target=null,moved=false;badge.setPointerCapture?.(event.pointerId);
    const move=(moveEvent)=>{
      if(Math.hypot(moveEvent.clientX-startX,moveEvent.clientY-startY)<8&&!moved)return;
      moved=true;moveEvent.preventDefault();badge.classList.add('dragging');
      const next=document.elementFromPoint(moveEvent.clientX,moveEvent.clientY)?.closest('.calendar-day[data-date]');
      if(next===target)return;if(target)target.classList.remove('drop-target');target=next;if(target)target.classList.add('drop-target');
    };
    const finish=async()=>{
      badge.removeEventListener('pointermove',move);if(target)target.classList.remove('drop-target');badge.classList.remove('dragging');
      if(moved&&target){badge.dataset.justDragged='1';setTimeout(()=>delete badge.dataset.justDragged,300);await moveScheduledPublication(item,target.dataset.date);}
    };
    badge.addEventListener('pointermove',move);badge.addEventListener('pointerup',finish,{once:true});badge.addEventListener('pointercancel',finish,{once:true});
  });
}
async function moveScheduledPublication(item,dateKey){
  const [year,month,day]=dateKey.split('-').map(Number),current=eventDate(item);const next=new Date(year,month-1,day,current.getHours(),current.getMinutes(),current.getSeconds());
  if(next.getTime()===current.getTime())return;
  try{await api(`/api/publications/${item.id}/schedule`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({scheduled_for:next.toISOString()})});setNotice('calendarNotice',`Publication déplacée au ${next.toLocaleString('fr-FR')} ✅`,'success');await loadCalendar();}
  catch(err){setNotice('calendarNotice',err.message,'error');}
}
function renderCalendar(items,start,end){
  const grid=$('calendarGrid'),list=$('calendarList');grid.innerHTML='';list.innerHTML='';
  if(calendarView==='month'){
    const firstOffset=(start.getDay()+6)%7;for(let index=0;index<firstOffset;index++){const blank=document.createElement('div');blank.className='calendar-day empty';grid.appendChild(blank);}
    for(let date=new Date(start);date<end;date.setDate(date.getDate()+1))grid.appendChild(calendarDayCell(new Date(date),items));
  }else if(calendarView==='week'){
    for(let date=new Date(start);date<end;date.setDate(date.getDate()+1))grid.appendChild(calendarDayCell(new Date(date),items));
  }
  if(!items.length){list.innerHTML=`<p class="muted">Aucune publication ${calendarView==='week'?'cette semaine':'ce mois-ci'}.</p>`;return;}
  for(const item of [...items].sort((a,b)=>eventDate(a)-eventDate(b))){
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
$('calendarPrev').addEventListener('click',()=>{if(calendarView==='week')calendarCursor.setDate(calendarCursor.getDate()-7);else calendarCursor.setMonth(calendarCursor.getMonth()-1);loadCalendar();});
$('calendarNext').addEventListener('click',()=>{if(calendarView==='week')calendarCursor.setDate(calendarCursor.getDate()+7);else calendarCursor.setMonth(calendarCursor.getMonth()+1);loadCalendar();});
$('calendarToday').addEventListener('click',()=>{calendarCursor=new Date();if(calendarView!=='week')calendarCursor.setDate(1);loadCalendar();});
document.querySelectorAll('[data-calendar-view]').forEach(button=>button.addEventListener('click',()=>{calendarView=button.dataset.calendarView;if(calendarView!=='week')calendarCursor.setDate(1);try{localStorage.setItem('igstudio.calendarView',calendarView);}catch{}loadCalendar();}));

function formatStat(value){return new Intl.NumberFormat('fr-FR',{notation:Number(value)>=10000?'compact':'standard',maximumFractionDigits:1}).format(Number(value)||0);}
function statsPeriodValue(){return Number($('statsPeriod').value)||30;}
function statsDate(value){
  const date=new Date(value||'');
  return Number.isNaN(date.getTime())?'—':date.toLocaleDateString('fr-FR',{day:'numeric',month:'short',year:'numeric'});
}
function renderPeriodComparison(comparison={}){
  const current=comparison.current||{};const previous=comparison.previous||{};const changes=comparison.changes||{};
  $('statsPeriodDates').textContent=`${statsDate(current.start)} – ${statsDate(current.end)} comparé à ${statsDate(previous.start)} – ${statsDate(previous.end)}`;
  const root=$('statsComparison');root.innerHTML='';
  const metrics=[
    ['Publications','media_count',value=>formatStat(value)],
    ['Vues','views',value=>formatStat(value)],
    ['Portée','reach',value=>formatStat(value)],
    ['Engagement','engagement_rate',value=>`${Number(value||0).toFixed(1)} %`]
  ];
  for(const [label,key,formatter] of metrics){
    const card=document.createElement('article');card.className='stats-comparison-item';
    card.innerHTML='<span class="label"></span><strong class="current"></strong><small class="previous"></small><span class="change"></span>';
    card.querySelector('.label').textContent=label;
    card.querySelector('.current').textContent=formatter(current[key]);
    card.querySelector('.previous').textContent=`Avant : ${formatter(previous[key])}`;
    const change=card.querySelector('.change');const value=changes[key];
    if(value===null||value===undefined){change.textContent='Pas de base comparable';change.classList.add('neutral');}
    else if(Math.abs(Number(value))<0.05){change.textContent='Stable';change.classList.add('neutral');}
    else{const rising=Number(value)>0;change.textContent=`${rising?'↑':'↓'} ${Math.abs(Number(value)).toFixed(1)} %`;change.classList.add(rising?'up':'down');}
    root.appendChild(card);
  }
}
function svgNode(name,attributes={}){
  const node=document.createElementNS('http://www.w3.org/2000/svg',name);
  for(const [key,value] of Object.entries(attributes))node.setAttribute(key,String(value));
  return node;
}
function renderGrowthChart(series=[]){
  const root=$('statsGrowthChart');root.innerHTML='';const meta=$('statsGrowthMeta');meta.textContent='';
  if(!Array.isArray(series)||series.length<2){
    const empty=document.createElement('p');empty.className='muted';empty.textContent=series.length?'Un premier relevé existe. Synchronise de nouveau plus tard pour tracer l’évolution.':'Aucun relevé historique enregistré pour cette période.';root.appendChild(empty);return;
  }
  const width=720,height=260,pad={left:62,right:20,top:20,bottom:42};
  const plotWidth=width-pad.left-pad.right,plotHeight=height-pad.top-pad.bottom;
  const maximum=Math.max(...series.flatMap(item=>[Number(item.views)||0,Number(item.reach)||0]),1);
  const x=index=>pad.left+index/(series.length-1)*plotWidth;
  const y=value=>pad.top+plotHeight-(Number(value)||0)/maximum*plotHeight;
  const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':'Évolution des vues et de la portée entre les synchronisations'});
  for(let step=0;step<=4;step++){
    const gridY=pad.top+step/4*plotHeight;
    svg.appendChild(svgNode('line',{x1:pad.left,y1:gridY,x2:width-pad.right,y2:gridY,class:'stats-chart-grid'}));
    const label=svgNode('text',{x:pad.left-10,y:gridY+4,'text-anchor':'end',class:'stats-chart-axis'});label.textContent=formatStat(maximum*(1-step/4));svg.appendChild(label);
  }
  for(const [key,className] of [['views','views'],['reach','reach']]){
    const points=series.map((item,index)=>`${x(index)},${y(item[key])}`).join(' ');
    svg.appendChild(svgNode('polyline',{points,class:`stats-chart-line ${className}`}));
    if(series.length<=20){
      series.forEach((item,index)=>{const circle=svgNode('circle',{cx:x(index),cy:y(item[key]),r:4,class:`stats-chart-point ${className}`});const title=svgNode('title');title.textContent=`${statsDate(item.captured_at)} : ${formatStat(item[key])}`;circle.appendChild(title);svg.appendChild(circle);});
    }
  }
  const firstLabel=svgNode('text',{x:pad.left,y:height-12,'text-anchor':'start',class:'stats-chart-axis'});firstLabel.textContent=statsDate(series[0].captured_at);svg.appendChild(firstLabel);
  const lastLabel=svgNode('text',{x:width-pad.right,y:height-12,'text-anchor':'end',class:'stats-chart-axis'});lastLabel.textContent=statsDate(series[series.length-1].captured_at);svg.appendChild(lastLabel);
  root.appendChild(svg);
  const last=series[series.length-1];const signed=value=>`${Number(value)>0?'+':''}${formatStat(value)}`;
  meta.textContent=`${series.length} point(s) de relevé • ${signed(last.delta_views)} vues • ${signed(last.delta_reach)} de portée depuis le premier point`;
}
function analyticsPostLabel(item){return item.hook||item.title||'Sans hook enregistré';}
function formatStatDuration(milliseconds){
  const seconds=Math.max(0,Number(milliseconds)||0)/1000;
  if(seconds<60)return `${seconds.toLocaleString('fr-FR',{maximumFractionDigits:1})} s`;
  const minutes=Math.floor(seconds/60);const remainder=Math.round(seconds%60);
  return `${minutes} min ${String(remainder).padStart(2,'0')} s`;
}
function formatStatRate(value){return `${Number(value||0).toLocaleString('fr-FR',{minimumFractionDigits:1,maximumFractionDigits:2})} %`;}
function formatSkipRate(value){const number=Number(value)||0;return formatStatRate(Math.abs(number)<=1?number*100:number);}
function appendPostDetail(root,label,value,help=''){
  const card=document.createElement('div');card.className='stats-post-detail';
  const labelNode=document.createElement('span');labelNode.textContent=label;
  const valueNode=document.createElement('strong');valueNode.textContent=value;
  card.append(labelNode,valueNode);
  if(help){const helpNode=document.createElement('small');helpNode.textContent=help;card.appendChild(helpNode);}
  root.appendChild(card);
}
function appendPostDelta(root,label,value){
  if(value===null||value===undefined)return;
  const number=Number(value)||0;
  appendPostDetail(root,label,`${number>0?'+':''}${formatStat(number)}`,number===0?'Stable depuis le relevé précédent':'Depuis le relevé précédent');
}
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
    const row=document.createElement('article');row.className='stats-post';row.innerHTML='<div class="stats-post-main"><span class="pill kind"></span><strong class="hook"></strong><span class="date"></span></div><div class="stats-post-metrics"><span class="views"></span><span class="likes"></span><span class="reach"></span><span class="rate"></span><span class="delta"></span></div><a class="ghost permalink" target="_blank" rel="noopener">Voir</a><details class="stats-post-details"><summary>Plus de statistiques</summary><div class="stats-post-detail-grid"></div></details>';
    row.querySelector('.kind').textContent={reel:'Reel',photo:'Photo',carousel:'Carrousel'}[item.media_kind]||'Post';
    row.querySelector('.hook').textContent=analyticsPostLabel(item);
    row.querySelector('.date').textContent=item.timestamp?new Date(item.timestamp).toLocaleString('fr-FR'):'Date indisponible';
    row.querySelector('.views').textContent=`${formatStat(item.views)} vues`;
    row.querySelector('.likes').textContent=`${formatStat(item.likes)} likes`;
    row.querySelector('.reach').textContent=`${formatStat(item.reach)} portée`;
    row.querySelector('.rate').textContent=`${Number(item.engagement_rate||0).toFixed(1)} % engagement`;
    const hasPrevious=item.delta_views!==null&&item.delta_views!==undefined;const delta=Number(item.delta_views||0);
    row.querySelector('.delta').textContent=!hasPrevious?'Premier relevé':delta?`${delta>0?'+':''}${formatStat(delta)} vues depuis le relevé précédent`:'Vues stables depuis le relevé précédent';
    const link=row.querySelector('.permalink');if(item.permalink)link.href=item.permalink;else link.classList.add('hidden');
    const available=new Set(Array.isArray(item.available_metrics)?item.available_metrics:[]);const details=row.querySelector('.stats-post-detail-grid');const rateBasis=Number(item.reach)>0?'portée':'vues';
    appendPostDetail(details,'Interactions',formatStat(item.interactions),`Taux global : ${formatStatRate(item.engagement_rate)}`);
    if(available.has('comments'))appendPostDetail(details,'Commentaires',formatStat(item.comments),`Taux / ${rateBasis} : ${formatStatRate(item.comment_rate)}`);
    if(available.has('saved'))appendPostDetail(details,'Enregistrements',formatStat(item.saved),`Taux / ${rateBasis} : ${formatStatRate(item.save_rate)}`);
    if(available.has('shares'))appendPostDetail(details,'Partages',formatStat(item.shares),`Taux / ${rateBasis} : ${formatStatRate(item.share_rate)}`);
    if(available.has('likes'))appendPostDetail(details,"Taux de J’aime",formatStatRate(item.like_rate),`Calculé sur la ${rateBasis}`);
    if(Number(item.reach)>0&&Number(item.views)>0)appendPostDetail(details,'Vues par compte touché',Number(item.views_per_reached_account||0).toLocaleString('fr-FR',{minimumFractionDigits:1,maximumFractionDigits:2}),'Plus de 1 peut inclure des relectures');
    if(available.has('ig_reels_avg_watch_time'))appendPostDetail(details,'Visionnage moyen',formatStatDuration(item.avg_watch_time_ms),'Statistique Reel');
    if(available.has('ig_reels_video_view_total_time'))appendPostDetail(details,'Temps regardé total',formatStatDuration(item.total_watch_time_ms),'Statistique Reel');
    if(available.has('clips_replays_count'))appendPostDetail(details,'Relectures',formatStat(item.replays),'Statistique Reel');
    if(available.has('reels_skip_rate'))appendPostDetail(details,'Taux de passage',formatSkipRate(item.skip_rate),'Statistique Reel fournie par Meta');
    appendPostDelta(details,'Évolution des vues',item.delta_views);
    appendPostDelta(details,'Évolution de la portée',item.delta_reach);
    appendPostDelta(details,"Évolution des J’aime",item.delta_likes);
    appendPostDelta(details,'Évolution des commentaires',item.delta_comments);
    appendPostDelta(details,'Évolution des enregistrements',item.delta_saved);
    appendPostDelta(details,'Évolution des partages',item.delta_shares);
    appendPostDelta(details,'Évolution des interactions',item.delta_interactions);
    row.querySelector('.stats-post-details summary').textContent=`Plus de statistiques (${details.children.length})`;
    posts.appendChild(row);
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
  renderPeriodComparison(data.period_comparison||{});
  renderGrowthChart(data.growth_series||[]);

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
  try{const data=await api(`/api/analytics/dashboard?period_days=${statsPeriodValue()}`);renderAnalytics(data);}
  catch(err){setNotice('statsNotice',err.message,'error');}
}
let analyticsSyncPollTimer=null;
function renderAnalyticsSyncProgress(progress={}){
  const root=$('statsSyncProgress'),phase=progress.phase||'idle';
  root.classList.toggle('hidden',phase==='idle');
  root.classList.toggle('failed',phase==='failed');
  root.classList.toggle('complete',phase==='complete');
  if(phase==='idle')return;
  const percent=Math.max(0,Math.min(100,Number(progress.percent)||0));
  $('statsSyncProgressBar').value=percent;
  $('statsSyncProgressPercent').textContent=`${percent.toLocaleString('fr-FR',{maximumFractionDigits:1})} %`;
  $('statsSyncProgressMessage').textContent=progress.message||'Synchronisation Instagram…';
  const total=Number(progress.total)||0,current=Number(progress.current)||0;
  const phaseLabels={preparing:'Préparation',listing:'Liste des publications',insights:'Statistiques Meta',saving:'Enregistrement',complete:'Terminé',failed:'Interrompu'};
  $('statsSyncProgressDetail').textContent=total&&['insights','saving','complete'].includes(phase)?`${current} publication(s) sur ${total} • ${phaseLabels[phase]||phase}`:phaseLabels[phase]||'Synchronisation en cours';
  const button=$('syncStatsBtn');button.disabled=Boolean(progress.running);button.textContent=progress.running?'Synchronisation…':'Synchroniser avec Instagram';
}
function stopAnalyticsSyncPolling(){
  if(analyticsSyncPollTimer){clearTimeout(analyticsSyncPollTimer);analyticsSyncPollTimer=null;}
}
async function pollAnalyticsSyncProgress(){
  stopAnalyticsSyncPolling();
  try{
    const data=await api('/api/analytics/sync-progress'),progress=data.progress||{};
    renderAnalyticsSyncProgress(progress);
    if(progress.running)analyticsSyncPollTimer=setTimeout(pollAnalyticsSyncProgress,500);
  }catch(err){
    if($('syncStatsBtn').disabled)analyticsSyncPollTimer=setTimeout(pollAnalyticsSyncProgress,1000);
  }
}
async function resumeAnalyticsSyncProgress(){await pollAnalyticsSyncProgress();}
$('syncStatsBtn').addEventListener('click',async()=>{
  if(!confirm('Synchroniser maintenant les statistiques de tes publications depuis Meta ?'))return;
  const button=$('syncStatsBtn');button.disabled=true;button.textContent='Synchronisation…';hideNotice('statsNotice');
  renderAnalyticsSyncProgress({running:true,phase:'preparing',percent:1,current:0,total:0,message:'Connexion à Instagram…'});
  analyticsSyncPollTimer=setTimeout(pollAnalyticsSyncProgress,250);
  try{
    const data=await api('/api/analytics/sync',{method:'POST'});
    await pollAnalyticsSyncProgress();
    await loadAnalytics();
    if(data.sync.permission_required)setNotice('statsNotice',data.sync.last_error,'error');
    else setNotice('statsNotice',`${data.sync.metrics_updated} publication(s) synchronisée(s).`,'success');
  }catch(err){renderAnalyticsSyncProgress({running:false,phase:'failed',percent:$('statsSyncProgressBar').value,current:0,total:0,message:err.message});setNotice('statsNotice',err.message,'error');}
  finally{stopAnalyticsSyncPolling();button.disabled=false;button.textContent='Synchroniser avec Instagram';}
});
$('analyzeStatsBtn').addEventListener('click',async()=>{
  if(!confirm('Envoyer à Groq uniquement les chiffres agrégés et caractéristiques anonymisées des hooks pour générer ton analyse ?'))return;
  const button=$('analyzeStatsBtn');button.disabled=true;button.textContent='Analyse…';hideNotice('statsAssistantNotice');
  try{
    const data=await api(`/api/analytics/assistant?period_days=${statsPeriodValue()}`,{method:'POST'});
    renderAssistantReport(data.report,new Date().toISOString());
    setNotice('statsAssistantNotice',`Analyse terminée avec ${data.model}.`,'success');
  }catch(err){setNotice('statsAssistantNotice',err.message,'error');}
  finally{button.disabled=false;button.textContent='Analyser mes performances';}
});
$('statsSort').addEventListener('change',renderAnalyticsPosts);
$('statsPeriod').addEventListener('change',()=>{try{localStorage.setItem('igstudio.statsPeriod',$('statsPeriod').value);}catch{}loadAnalytics();});
try{const savedPeriod=localStorage.getItem('igstudio.statsPeriod');if(['7','30','90'].includes(savedPeriod))$('statsPeriod').value=savedPeriod;}catch{}

function renderAssistantChat(messages=[]){
  const root=$('assistantChatMessages');root.innerHTML='';
  if(!messages.length){root.innerHTML='<p class="muted">Commence par poser une question sur tes statistiques.</p>';return;}
  for(const message of messages){
    const bubble=document.createElement('article');bubble.className=`assistant-chat-message ${message.role==='user'?'user':'assistant'}`;
    const label=document.createElement('strong');label.textContent=message.role==='user'?'Toi':'Assistant Groq';
    const content=document.createElement('p');content.textContent=message.content||'';bubble.append(label,content);root.appendChild(bubble);
  }
  root.scrollTop=root.scrollHeight;
}
async function loadAssistantChat(){
  try{const data=await api('/api/analytics/assistant/chat');renderAssistantChat(data.messages||[]);}
  catch(err){setNotice('assistantChatNotice',err.message,'error');}
}
$('assistantChatForm').addEventListener('submit',async event=>{
  event.preventDefault();const input=$('assistantChatInput');const message=input.value.trim();if(!message)return;
  const button=$('assistantChatSend');button.disabled=true;button.textContent='Réflexion…';hideNotice('assistantChatNotice');
  try{
    await api(`/api/analytics/assistant/chat?period_days=${statsPeriodValue()}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message})});
    input.value='';await loadAssistantChat();
  }catch(err){setNotice('assistantChatNotice',err.message,'error');}
  finally{button.disabled=false;button.textContent='Envoyer à l’assistant';}
});
$('clearAssistantChat').addEventListener('click',async()=>{
  if(!confirm('Effacer tout l’historique de conversation de l’assistant ?'))return;
  try{await api('/api/analytics/assistant/chat',{method:'DELETE'});renderAssistantChat([]);setNotice('assistantChatNotice','Historique effacé.','success');}
  catch(err){setNotice('assistantChatNotice',err.message,'error');}
});

function urlBase64ToUint8Array(base64String){const padding='='.repeat((4-base64String.length%4)%4);const base64=(base64String+padding).replace(/-/g,'+').replace(/_/g,'/');const raw=atob(base64);return Uint8Array.from([...raw].map(char=>char.charCodeAt(0)));}
function pushPreferences(){return {before_publication:$('notifyBefore').checked,published:$('notifyPublished').checked,failed:$('notifyFailed').checked,manual_music:$('notifyMusic').checked,studio_login:$('notifyLogin').checked,instagram_token:$('notifyToken').checked,security_lockout:$('notifySecurityLockout').checked};}
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
if(window.matchMedia('(max-width:760px)').matches){
  document.querySelector('.nested-collapsible')?.removeAttribute('open');
  document.querySelectorAll('details.collapsible-card')[1]?.removeAttribute('open');
}
updateDraftCount();configureMediaKind(false);updatePublicationOptions();refreshStudioSoundSetting();loadV2Status();
const requestedTab=new URLSearchParams(location.search).get('tab');if(requestedTab)activateTab(requestedTab);
