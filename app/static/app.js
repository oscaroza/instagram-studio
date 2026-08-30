const $ = (id) => document.getElementById(id);
const fields = [
  'mediaKind','mediaItemsJson','videoUrl','libraryId','thumbnailUrl','description','location','drone','language',
  'tone','extra','calendarEntryTitle','caption','hashtags','altText','hook','publicationMode'
];
const statusLabels = {
  scheduled:'Programmée', publishing:'Publication en cours', published:'Publiée',
  failed:'Échec', cancelled:'Annulée', awaiting_manual:'À finaliser dans Instagram'
};
let calendarCursor = new Date();
let calendarView = (()=>{try{return localStorage.getItem('igstudio.calendarView')||'month';}catch{return 'month';}})();
let calendarListRange = (()=>{try{return localStorage.getItem('igstudio.calendarListRange')||'month';}catch{return 'month';}})();
if(!['month','week','list'].includes(calendarView))calendarView='month';
if(!['month','week','today'].includes(calendarListRange))calendarListRange='month';
if(calendarView==='month'||(calendarView==='list'&&calendarListRange==='month'))calendarCursor.setDate(1);
let selectedMediaItems = [];
let imageEditorItemIndex = -1;
let imageEditorImage = null;
let imageEditorObjectUrl = '';
let imageEditorState = null;
let imageEditorSelectedLayerId = '';
let imageEditorDrag = null;
let libraryItems = [];
let analyticsPosts = [];
let autopilotItems = [];
let preparedInstagramShare = null;
const autopilotStatusLabels={queued:'Prête à analyser',analyzing:'Analyse en cours',analyzed:'Analysée',analysis_failed:'Analyse à relancer',planned:'Proposition disponible',scheduled:'Programmée'};
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
let studioRealtimeSource = null;
let calendarRealtimeTimer = null;
let calendarEventVersion = 0;
let calendarRenderedVersion = 0;
let calendarLoadPromise = null;
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
  if(studioRealtimeSource){studioRealtimeSource.close();studioRealtimeSource=null;}
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
  if(document.visibilityState!=='visible'){disconnectStudioRealtime();return;}
  if(Date.now()-lastActivityAt>=SESSION_IDLE_MS)expireStudioSession();
  else{
    registerActivity();
    if(calendarPanelIsActive()){
      connectStudioRealtime();
      calendarEventVersion+=1;
      scheduleRealtimeCalendarRefresh();
    }
  }
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
  if(name==='autopilot')loadAutopilotQueue();
  if(name==='settings'){loadPublishingLimit();loadInstagramTokenHealth();loadLoginHistory();loadPasskeys();loadR2Usage();}
  if(name==='customize')loadAppearance();
  if(name==='library')loadLibrary();
  if(name==='calendar'){connectStudioRealtime();loadCalendar();}
  else disconnectStudioRealtime();
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

const IMAGE_EDITOR_FONTS=['Arial','Arial Black','Georgia','Impact','Trebuchet MS','Verdana','Courier New','Times New Roman'];
const IMAGE_EDITOR_MAX_LAYERS=20;
function cleanHexColor(value,fallback){return /^#[0-9a-f]{6}$/i.test(String(value||''))?String(value):fallback;}
function imageEditorNumber(value,fallback){const parsed=Number(value);return Number.isFinite(parsed)?parsed:fallback;}
function imageEditorCleanLayer(raw={}){
  return {
    id:String(raw.id||`text-${Date.now()}-${Math.random().toString(16).slice(2)}`).slice(0,80),
    text:String(raw.text??'Ton texte').slice(0,240),
    x:Math.max(0,Math.min(1,imageEditorNumber(raw.x,.5))),y:Math.max(0,Math.min(1,imageEditorNumber(raw.y,.5))),
    size:Math.max(2,Math.min(24,imageEditorNumber(raw.size,8))),rotation:Math.max(-180,Math.min(180,imageEditorNumber(raw.rotation,0))),
    font:IMAGE_EDITOR_FONTS.includes(raw.font)?raw.font:'Arial Black',align:['left','center','right'].includes(raw.align)?raw.align:'center',
    color:cleanHexColor(raw.color,'#ffffff'),strokeColor:cleanHexColor(raw.strokeColor,'#000000'),
    strokeWidth:Math.max(0,Math.min(5,Number(raw.strokeWidth)||0)),backgroundColor:cleanHexColor(raw.backgroundColor,'#000000'),
    backgroundOpacity:Math.max(0,Math.min(.9,Number(raw.backgroundOpacity)||0)),shadow:Boolean(raw.shadow)
  };
}
function imageEditorCleanState(raw){
  const layers=Array.isArray(raw?.layers)?raw.layers.slice(0,IMAGE_EDITOR_MAX_LAYERS).map(imageEditorCleanLayer):[];
  return {version:1,layers};
}
function selectedImageEditorLayer(){return imageEditorState?.layers.find(layer=>layer.id===imageEditorSelectedLayerId)||null;}
function imageEditorSetNotice(message,type=''){
  if(!message){hideNotice('imageEditorNotice');return;}
  setNotice('imageEditorNotice',message,type);
}
function imageEditorSyncControls(){
  const layer=selectedImageEditorLayer(),disabled=!layer;
  for(const id of ['imageEditorText','imageEditorFont','imageEditorAlign','imageEditorSize','imageEditorRotation','imageEditorColor','imageEditorStrokeColor','imageEditorBackgroundColor','imageEditorStrokeWidth','imageEditorBackgroundOpacity','imageEditorShadow','duplicateTextLayerBtn','deleteTextLayerBtn'])$(id).disabled=disabled;
  if(layer){
    $('imageEditorText').value=layer.text;$('imageEditorFont').value=layer.font;$('imageEditorAlign').value=layer.align;
    $('imageEditorSize').value=layer.size;$('imageEditorRotation').value=layer.rotation;$('imageEditorColor').value=layer.color;
    $('imageEditorStrokeColor').value=layer.strokeColor;$('imageEditorBackgroundColor').value=layer.backgroundColor;
    $('imageEditorStrokeWidth').value=String(layer.strokeWidth);$('imageEditorBackgroundOpacity').value=String(layer.backgroundOpacity);$('imageEditorShadow').checked=layer.shadow;
  }else $('imageEditorText').value='';
  $('imageEditorSizeValue').textContent=`${layer?.size||0} %`;$('imageEditorRotationValue').textContent=`${layer?.rotation||0}°`;
  const root=$('imageEditorLayers');root.innerHTML='';
  for(const [index,item] of (imageEditorState?.layers||[]).entries()){
    const button=document.createElement('button');button.type='button';button.className=`ghost${item.id===imageEditorSelectedLayerId?' active':''}`;
    button.textContent=`${index+1}. ${item.text.trim().split('\n')[0]||'Texte vide'}`;button.onclick=()=>{imageEditorSelectedLayerId=item.id;imageEditorSyncControls();drawImageEditor();};root.appendChild(button);
  }
}
function imageEditorLayerMetrics(ctx,layer){
  const fontSize=Math.max(12,layer.size*ctx.canvas.width/100),lines=(layer.text||' ').split('\n').slice(0,8),lineHeight=fontSize*1.15;
  ctx.font=`900 ${fontSize}px "${layer.font}"`;const width=Math.max(fontSize,...lines.map(line=>ctx.measureText(line||' ').width));
  return {fontSize,lines,lineHeight,width,height:lineHeight*lines.length,padding:fontSize*.24};
}
function drawImageEditor(showSelection=true){
  const canvas=$('imageEditorCanvas'),ctx=canvas.getContext('2d');if(!imageEditorImage||!canvas.width)return;
  ctx.clearRect(0,0,canvas.width,canvas.height);ctx.drawImage(imageEditorImage,0,0,canvas.width,canvas.height);
  for(const layer of imageEditorState.layers){
    ctx.save();const cx=layer.x*canvas.width,cy=layer.y*canvas.height;ctx.translate(cx,cy);ctx.rotate(layer.rotation*Math.PI/180);
    const metrics=imageEditorLayerMetrics(ctx,layer),boxWidth=metrics.width+metrics.padding*2,boxHeight=metrics.height+metrics.padding*2;
    layer._bounds={width:boxWidth,height:boxHeight};
    if(layer.backgroundOpacity>0){ctx.globalAlpha=layer.backgroundOpacity;ctx.fillStyle=layer.backgroundColor;ctx.fillRect(-boxWidth/2,-boxHeight/2,boxWidth,boxHeight);ctx.globalAlpha=1;}
    ctx.font=`900 ${metrics.fontSize}px "${layer.font}"`;ctx.textBaseline='top';ctx.textAlign=layer.align;ctx.lineJoin='round';
    if(layer.shadow){ctx.shadowColor='rgba(0,0,0,.72)';ctx.shadowBlur=metrics.fontSize*.18;ctx.shadowOffsetY=metrics.fontSize*.08;}
    const textX=layer.align==='left'?-metrics.width/2:layer.align==='right'?metrics.width/2:0;
    metrics.lines.forEach((line,index)=>{
      const y=-metrics.height/2+index*metrics.lineHeight;
      if(layer.strokeWidth>0){ctx.strokeStyle=layer.strokeColor;ctx.lineWidth=Math.max(1,layer.strokeWidth*metrics.fontSize/28);ctx.strokeText(line,textX,y);}
      ctx.fillStyle=layer.color;ctx.fillText(line,textX,y);
    });
    ctx.shadowColor='transparent';
    if(showSelection&&layer.id===imageEditorSelectedLayerId){ctx.strokeStyle='#ffffff';ctx.lineWidth=Math.max(2,canvas.width/500);ctx.setLineDash([canvas.width/160,canvas.width/220]);ctx.strokeRect(-boxWidth/2,-boxHeight/2,boxWidth,boxHeight);}
    ctx.restore();
  }
}
function imageEditorPoint(event){
  const canvas=$('imageEditorCanvas'),bounds=canvas.getBoundingClientRect();return {x:(event.clientX-bounds.left)*canvas.width/bounds.width,y:(event.clientY-bounds.top)*canvas.height/bounds.height};
}
function imageEditorHitLayer(point){
  const canvas=$('imageEditorCanvas');
  for(let index=imageEditorState.layers.length-1;index>=0;index--){
    const layer=imageEditorState.layers[index],dx=point.x-layer.x*canvas.width,dy=point.y-layer.y*canvas.height,angle=-layer.rotation*Math.PI/180;
    const localX=dx*Math.cos(angle)-dy*Math.sin(angle),localY=dx*Math.sin(angle)+dy*Math.cos(angle),bounds=layer._bounds||{width:0,height:0};
    if(Math.abs(localX)<=bounds.width/2&&Math.abs(localY)<=bounds.height/2)return layer;
  }
  return null;
}
function addImageEditorLayer(copy=null){
  if(imageEditorState.layers.length>=IMAGE_EDITOR_MAX_LAYERS){imageEditorSetNotice('Maximum 20 textes par image.','error');return;}
  const layer=imageEditorCleanLayer(copy?{...copy,id:'',x:Math.min(.95,copy.x+.04),y:Math.min(.95,copy.y+.04)}:{});
  imageEditorState.layers.push(layer);imageEditorSelectedLayerId=layer.id;imageEditorSyncControls();drawImageEditor();$('imageEditorText').focus();
}
function closeImageTextEditor(){
  $('imageTextEditor').classList.add('hidden');document.body.classList.remove('image-editor-open');imageEditorDrag=null;imageEditorImage=null;imageEditorState=null;imageEditorItemIndex=-1;
  if(imageEditorObjectUrl){URL.revokeObjectURL(imageEditorObjectUrl);imageEditorObjectUrl='';}
}
async function openImageTextEditor(index){
  const item=selectedMediaItems[index];if(!item||item.media_type!=='image')return;
  imageEditorItemIndex=index;imageEditorState=imageEditorCleanState(item.text_editor);imageEditorSelectedLayerId=imageEditorState.layers.at(-1)?.id||'';
  $('restoreOriginalImageBtn').disabled=!item.original_url;
  $('imageTextEditor').classList.remove('hidden');document.body.classList.add('image-editor-open');$('imageEditorEmpty').classList.remove('hidden');$('imageEditorCanvas').classList.add('hidden');imageEditorSetNotice('');
  try{
    let source=item.original_url||item.url;const parsedSource=new URL(source,location.href);
    if(parsedSource.pathname.startsWith('/media/'))source=`${location.origin}${parsedSource.pathname}${parsedSource.search}`;
    const response=await fetch(source);if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const blob=await response.blob();imageEditorObjectUrl=URL.createObjectURL(blob);const image=new Image();
    await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(new Error('Format d’image non lisible.'));image.src=imageEditorObjectUrl;});
    imageEditorImage=image;const maxEdge=2160,ratio=Math.min(1,maxEdge/Math.max(image.naturalWidth,image.naturalHeight)),canvas=$('imageEditorCanvas');
    canvas.width=Math.max(1,Math.round(image.naturalWidth*ratio));canvas.height=Math.max(1,Math.round(image.naturalHeight*ratio));
    $('imageEditorEmpty').classList.add('hidden');canvas.classList.remove('hidden');if(!imageEditorState.layers.length)addImageEditorLayer();else{imageEditorSyncControls();drawImageEditor();}
  }catch(error){imageEditorSetNotice(`Impossible de charger cette image dans l’éditeur : ${error.message}`,'error');}
}
async function saveImageTextEditor(){
  const item=selectedMediaItems[imageEditorItemIndex];if(!item||!imageEditorImage)return;
  const button=$('saveImageEditorBtn');button.disabled=true;button.textContent='Préparation…';imageEditorSetNotice('Création de la nouvelle image…');drawImageEditor(false);
  try{
    const canvas=$('imageEditorCanvas'),blob=await new Promise((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('Export de l’image impossible.')),'image/jpeg',.94));
    const baseName=(item.name||'photo').replace(/\.[^.]+$/,'').replace(/-texte$/,'');const file=new File([blob],`${baseName}-texte.jpg`,{type:'image/jpeg'});const startedAt=performance.now();
    const data=await uploadMediaFile(file,(loaded,total)=>{const percent=total?Math.min(100,loaded/total*100):0;button.textContent=`Envoi ${percent.toLocaleString('fr-FR',{maximumFractionDigits:0})} %`;imageEditorSetNotice(`Envoi de l’image modifiée : ${formatBytes(loaded)} / ${formatBytes(total)}`);});
    selectedMediaItems[imageEditorItemIndex]={...item,url:data.url,library_id:'',thumbnail_url:data.url,media_type:'image',name:file.name,size:data.size,
      original_url:item.original_url||item.url,original_library_id:item.original_library_id??item.library_id??'',original_thumbnail_url:item.original_thumbnail_url||item.thumbnail_url||item.url,original_size:item.original_size||item.size||0,
      text_editor:imageEditorCleanState(imageEditorState),edited:true};
    syncMediaFields();renderSelectedMedia();closeImageTextEditor();setNotice('uploadProgress','Image modifiée prête. L’original est conservé pour pouvoir la rééditer.','success');
  }catch(error){drawImageEditor();imageEditorSetNotice(error.message,'error');}
  finally{button.disabled=false;button.textContent='Enregistrer l’image';}
}
function restoreOriginalImage(){
  const item=selectedMediaItems[imageEditorItemIndex];if(!item?.original_url)return;
  selectedMediaItems[imageEditorItemIndex]={...item,url:item.original_url,library_id:item.original_library_id||'',thumbnail_url:item.original_thumbnail_url||item.original_url,name:(item.name||'photo-texte.jpg').replace(/-texte(?=\.[^.]+$)/,''),size:item.original_size||item.size};
  delete selectedMediaItems[imageEditorItemIndex].text_editor;delete selectedMediaItems[imageEditorItemIndex].edited;delete selectedMediaItems[imageEditorItemIndex].original_url;delete selectedMediaItems[imageEditorItemIndex].original_library_id;delete selectedMediaItems[imageEditorItemIndex].original_thumbnail_url;delete selectedMediaItems[imageEditorItemIndex].original_size;
  syncMediaFields();renderSelectedMedia();closeImageTextEditor();setNotice('uploadProgress','Image originale restaurée.','success');
}
function updateImageEditorLayerFromControls(){
  const layer=selectedImageEditorLayer();if(!layer)return;
  layer.text=$('imageEditorText').value.slice(0,240);layer.font=$('imageEditorFont').value;layer.align=$('imageEditorAlign').value;layer.size=Number($('imageEditorSize').value);layer.rotation=Number($('imageEditorRotation').value);
  layer.color=$('imageEditorColor').value;layer.strokeColor=$('imageEditorStrokeColor').value;layer.backgroundColor=$('imageEditorBackgroundColor').value;layer.strokeWidth=Number($('imageEditorStrokeWidth').value);layer.backgroundOpacity=Number($('imageEditorBackgroundOpacity').value);layer.shadow=$('imageEditorShadow').checked;
  imageEditorSyncControls();drawImageEditor();
}
for(const id of ['imageEditorText','imageEditorFont','imageEditorAlign','imageEditorSize','imageEditorRotation','imageEditorColor','imageEditorStrokeColor','imageEditorBackgroundColor','imageEditorStrokeWidth','imageEditorBackgroundOpacity','imageEditorShadow'])$(id).addEventListener('input',updateImageEditorLayerFromControls);
$('addTextLayerBtn').onclick=()=>addImageEditorLayer();$('duplicateTextLayerBtn').onclick=()=>{const layer=selectedImageEditorLayer();if(layer)addImageEditorLayer(layer);};
$('deleteTextLayerBtn').onclick=()=>{if(!imageEditorSelectedLayerId)return;imageEditorState.layers=imageEditorState.layers.filter(layer=>layer.id!==imageEditorSelectedLayerId);imageEditorSelectedLayerId=imageEditorState.layers.at(-1)?.id||'';imageEditorSyncControls();drawImageEditor();};
$('closeImageEditorBtn').onclick=closeImageTextEditor;$('cancelImageEditorBtn').onclick=closeImageTextEditor;$('saveImageEditorBtn').onclick=saveImageTextEditor;$('restoreOriginalImageBtn').onclick=restoreOriginalImage;
$('imageEditorCanvas').addEventListener('pointerdown',event=>{const point=imageEditorPoint(event),layer=imageEditorHitLayer(point);imageEditorSelectedLayerId=layer?.id||'';imageEditorSyncControls();drawImageEditor();if(!layer)return;imageEditorDrag={id:layer.id,offsetX:point.x-layer.x*$('imageEditorCanvas').width,offsetY:point.y-layer.y*$('imageEditorCanvas').height};event.currentTarget.setPointerCapture?.(event.pointerId);event.preventDefault();});
$('imageEditorCanvas').addEventListener('pointermove',event=>{if(!imageEditorDrag)return;const layer=selectedImageEditorLayer();if(!layer)return;const point=imageEditorPoint(event),canvas=$('imageEditorCanvas');layer.x=Math.max(0,Math.min(1,(point.x-imageEditorDrag.offsetX)/canvas.width));layer.y=Math.max(0,Math.min(1,(point.y-imageEditorDrag.offsetY)/canvas.height));drawImageEditor();event.preventDefault();});
for(const eventName of ['pointerup','pointercancel'])$('imageEditorCanvas').addEventListener(eventName,()=>{imageEditorDrag=null;});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('imageTextEditor').classList.contains('hidden'))closeImageTextEditor();});
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
    row.innerHTML=`<span class="media-drag-handle" role="button" tabindex="0" aria-label="Déplacer ce média">⋮⋮</span><${previewTag} aria-label="Aperçu du média"></${previewTag}><div class="selected-media-details"><strong></strong><span></span></div><div class="selected-media-actions">${item.media_type==='image'?'<button class="secondary edit-image" type="button">Ajouter du texte</button>':''}<button class="ghost remove-media" type="button">Retirer</button></div>`;
    const preview=row.querySelector(previewTag);preview.src=item.thumbnail_url||item.url||'/static/icons/icon-192.png';
    if(previewTag==='video'){preview.muted=true;preview.playsInline=true;preview.preload='metadata';}
    row.querySelector('strong').textContent=item.name||`Média ${index+1}`;
    row.querySelector('.selected-media-details span').textContent=`Position ${index+1} • ${item.media_type==='image'?'Photo JPEG':'Vidéo'}${item.edited?' • Texte ajouté':''}${item.size?` • ${formatBytes(item.size)}`:''}`;
    const handle=row.querySelector('.media-drag-handle');handle.classList.toggle('hidden',!reorderable);
    handle.addEventListener('keydown',(event)=>{
      if(!reorderable||!['ArrowUp','ArrowDown'].includes(event.key))return;
      event.preventDefault();const target=index+(event.key==='ArrowUp'?-1:1);reorderSelectedMedia(index,target);
      root.children[Math.max(0,Math.min(target,root.children.length-1))]?.querySelector('.media-drag-handle')?.focus();
    });
    handle.addEventListener('pointerdown',(event)=>startMediaPointerDrag(event,row,root));
    row.querySelector('.edit-image')?.addEventListener('click',()=>openImageTextEditor(index));
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
  if(kind==='reel'||kind==='story_video'){
    file.accept='video/mp4,video/quicktime,video/x-m4v';file.multiple=false;
    $('mediaUploadLabel').textContent=kind==='story_video'?'Choisir la vidéo de la Story':'Choisir une vidéo';$('mediaUploadHelp').textContent='MP4 / MOV / M4V';$('mediaUrlLabel').textContent=kind==='story_video'?'URL publique de la Story vidéo':'URL vidéo publique';
  }else if(kind==='carousel_video'){
    file.accept='video/mp4,video/quicktime,video/x-m4v';file.multiple=true;
    $('mediaUploadLabel').textContent='Choisir 2 à 10 vidéos';$('mediaUploadHelp').textContent=`MP4 / MOV / M4V • max ${document.body.dataset.maxUploadMb||250} Mo par vidéo`;$('mediaUrlLabel').textContent='URL vidéo publique';
  }else{
    file.accept='image/jpeg';file.multiple=isCarouselMode(kind);
    $('mediaUploadLabel').textContent=kind==='carousel'?'Choisir 2 à 10 photos':kind==='story_photo'?'Choisir la photo de la Story':'Choisir une photo';$('mediaUploadHelp').textContent='JPG / JPEG • 8 Mo max par photo';$('mediaUrlLabel').textContent=kind==='story_photo'?'URL publique de la Story photo':'URL photo JPEG publique';
  }
  $('publicUrlBlock').classList.toggle('hidden',isCarouselMode(kind));
  $('publicationModeField').classList.toggle('hidden',kind!=='reel');
  const storyMode=kind==='story_photo'||kind==='story_video';
  $('musicOption').classList.toggle('hidden',storyMode);
  $('storyHelp').classList.toggle('hidden',!storyMode);
  $('muteOption').classList.toggle('hidden',kind!=='reel'&&kind!=='story_video');
  if(kind!=='reel')$('publicationMode').value='normal';
  if(kind!=='reel'&&kind!=='story_video')$('muteAudio').checked=false;
  if(storyMode)$('musicEnabled').checked=false;
  $('carouselOrderHelp').classList.toggle('hidden',!isCarouselMode(kind)||selectedMediaItems.length<2);
  updatePublicationOptions();
}
$('mediaKind').addEventListener('change',()=>configureMediaKind(true));
$('videoFile').addEventListener('change',async(e)=>{
  const fileInput=e.currentTarget,files=[...fileInput.files];if(!files.length)return;
  const kind=$('mediaKind').value,expected=kind==='reel'||kind==='carousel_video'||kind==='story_video'?'video':'image';
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
    const label=isCarouselMode(kind)?`${selectedMediaItems.length} ${kind==='carousel_video'?'vidéos':'photos'} prêtes`:kind==='photo'||kind==='story_photo'?'Photo prête':'Vidéo prête';
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
    el.querySelector('h3').textContent=(draft.calendarEntryTitle||draft.location||draft.description||'Brouillon').slice(0,70);
    el.querySelector('p').textContent=new Date(draft.savedAt).toLocaleString();
    el.querySelector('.load').onclick=()=>loadDraft(draft.id);el.querySelector('.del').onclick=()=>deleteDraft(draft.id);root.appendChild(el);
  }
}

function updatePublicationOptions(){
  const scheduled=$('scheduleEnabled').checked,music=$('musicEnabled').checked,kind=$('mediaKind').value;
  const typeLabel={reel:'le Reel',photo:'la photo',carousel:'le carrousel photo',carousel_video:'le carrousel vidéo',story_photo:'la Story photo',story_video:'la Story vidéo'}[kind];
  $('scheduleFields').classList.toggle('hidden',!scheduled);
  $('musicHelp').classList.toggle('hidden',!music);
  $('publishBtn').textContent=scheduled?`Programmer ${typeLabel}`:music?'Finaliser dans Instagram':`Publier ${typeLabel}`;
  $('publishBtn').className=scheduled?'primary':music?'secondary':'danger';
}
$('scheduleEnabled').addEventListener('change',updatePublicationOptions);
$('musicEnabled').addEventListener('change',updatePublicationOptions);

function publicationPayload(){
  const fullCaption=[$('hook').value.trim(),$('caption').value.trim(),$('hashtags').value.trim()].filter(Boolean).join('\n\n');
  const selectedKind=$('mediaKind').value,mediaKind=selectedKind==='carousel_video'?'carousel':selectedKind==='story_photo'||selectedKind==='story_video'?'story':selectedKind;
  let items=selectedMediaItems.map(item=>({...item}));
  const publicUrl=$('videoUrl').value.trim();
  if(!isCarouselMode(selectedKind)&&publicUrl&&(!items.length||items[0].url!==publicUrl)){
    const urlMediaType=mediaKind==='reel'||selectedKind==='story_video'?'video':'image';
    items=[{url:publicUrl,library_id:$('libraryId').value,thumbnail_url:$('thumbnailUrl').value,media_type:urlMediaType,name:urlMediaType==='video'?'Vidéo par URL':'Photo par URL'}];
  }
  return {
    title:($('calendarEntryTitle').value||$('location').value||$('description').value||'Publication Instagram').trim(),
    description:$('description').value.trim(),location:$('location').value.trim(),device:$('drone').value.trim(),
    media_kind:mediaKind,media_items:items,
    video_url:mediaKind==='reel'||mediaKind==='story'&&items[0]?.media_type==='video'?(items[0]?.url||''):'',image_url:mediaKind==='photo'||mediaKind==='story'&&items[0]?.media_type==='image'?(items[0]?.url||''):'',library_id:items[0]?.library_id||'',
    story_media_type:mediaKind==='story'?(items[0]?.media_type||''):'',
    thumbnail_url:$('thumbnailUrl').value,caption:fullCaption,hook:$('hook').value.trim(),
    alt_text:$('altText').value.trim(),publication_mode:$('publicationMode').value,
    workflow:$('musicEnabled').checked?'manual_music':'auto_publish',
    mute_audio:$('muteAudio').checked,
    timezone:Intl.DateTimeFormat().resolvedOptions().timeZone||'Europe/Paris'
  };
}

function validatePublicationMedia(payload){
  if(payload.media_kind==='carousel'&&(payload.media_items.length<2||payload.media_items.length>10))return 'Ajoute entre 2 et 10 médias au carrousel.';
  if(payload.media_kind!=='carousel'&&payload.media_items.length!==1)return 'Ajoute un média ou une URL avant de continuer.';
  return '';
}

async function ensureDurablePayloadMedia(payload,noticePrefix='Copie durable vers le stockage média'){
  const durableItems=payload.media_items.map(item=>({...item}));
  for(let index=0;index<durableItems.length;index++){
    const item=durableItems[index];
    if(item.library_id)continue;
    setNotice('actionMessage',`${noticePrefix} (${index+1}/${durableItems.length})…`);
    let sourceUrl=item.original_url||'',sourceLibraryId=item.original_library_id||'';
    if(item.media_type==='image'&&item.text_editor&&sourceUrl&&!sourceLibraryId){
      setNotice('actionMessage',`${noticePrefix} • original ${index+1}/${durableItems.length}…`);
      const original=await api('/api/library/promote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_url:sourceUrl,media_type:'image',description:`${payload.title} • original`})});
      sourceUrl=original.url;sourceLibraryId=original.media.id;
    }
    const promoted=await api('/api/library/promote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_url:item.url,media_type:item.media_type,mute_audio:payload.mute_audio,description:payload.title,text_editor:item.text_editor||null,source_url:sourceUrl,source_library_id:sourceLibraryId})});
    durableItems[index]={...item,url:promoted.url,library_id:promoted.media.id,thumbnail_url:promoted.media.thumbnail_url||item.thumbnail_url,original_url:sourceUrl||item.original_url,original_library_id:sourceLibraryId||item.original_library_id};
    payload.media_items=durableItems;selectedMediaItems=durableItems.map(mediaItem=>({...mediaItem}));syncMediaFields();renderSelectedMedia();
  }
  payload.media_items=durableItems;selectedMediaItems=durableItems.map(item=>({...item}));syncMediaFields();renderSelectedMedia();
  payload.library_id=durableItems.length===1?durableItems[0].library_id:'';
  payload.thumbnail_url=durableItems[0]?.thumbnail_url||'';
  if(payload.media_kind==='reel')payload.video_url=durableItems[0].url;
  if(payload.media_kind==='photo')payload.image_url=durableItems[0].url;
  if(payload.media_kind==='story'){
    payload.story_media_type=durableItems[0].media_type;
    if(durableItems[0].media_type==='video')payload.video_url=durableItems[0].url;
    else payload.image_url=durableItems[0].url;
  }
  return payload;
}

$('publishBtn').addEventListener('click',async()=>{
  const payload=publicationPayload();
  const mediaError=validatePublicationMedia(payload);if(mediaError){setNotice('actionMessage',mediaError,'error');return;}
  const scheduled=$('scheduleEnabled').checked,music=$('musicEnabled').checked;
  if(scheduled){
    if(!$('scheduledFor').value){setNotice('actionMessage','Choisis la date et l’heure.','error');return;}
    payload.scheduled_for=new Date($('scheduledFor').value).toISOString();
  }
  const kindLabel={reel:'ce Reel',photo:'cette photo',carousel:'ce carrousel',story:'cette Story'}[payload.media_kind];
  const question=scheduled?'Programmer cette publication ?':music?`Préparer ${kindLabel} pour Instagram ?`:`Publier ${kindLabel} maintenant sur Instagram ?`;
  const btn=$('publishBtn');btn.disabled=true;const oldLabel=btn.textContent;btn.textContent='Vérification…';hideNotice('preflightNotice');
  try{
    const preflight=await api('/api/publications/preflight',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    setNotice('preflightNotice',`Vérification réussie ✅\n${(preflight.checks||[]).map(check=>`• ${check}`).join('\n')}`,'success');
    if(!confirm(question))return;
    btn.textContent='En cours…';
    if(scheduled){
      await ensureDurablePayloadMedia(payload);
    }
    if(scheduled||music){
      const data=await api('/api/publications',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if(scheduled){setNotice('actionMessage','Publication programmée ✅','success');playStudioChime();activateTab('calendar');}
      else{
        await prepareInstagramFinalization(payload);
      }
      return data;
    }
    if((payload.media_kind==='reel'||payload.media_kind==='story'&&payload.media_items[0]?.media_type==='video')&&payload.mute_audio){
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

$('autopilotQueueBtn').addEventListener('click',async()=>{
  const payload=publicationPayload(),button=$('autopilotQueueBtn');
  const mediaError=validatePublicationMedia(payload);if(mediaError){setNotice('actionMessage',mediaError,'error');return;}
  button.disabled=true;const oldLabel=button.textContent;button.textContent='Ajout à la file…';hideNotice('actionMessage');
  try{
    await ensureDurablePayloadMedia(payload,'Enregistrement Auto-pilot dans la bibliothèque');
    await api('/api/autopilot/queue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    setNotice('actionMessage','Ajouté à la file Auto-pilot ✅','success');playStudioChime();activateTab('autopilot');
  }catch(err){setNotice('actionMessage',err.message,'error');}
  finally{button.disabled=false;button.textContent=oldLabel;}
});

function autopilotDateInputValue(value){
  const date=new Date(value||'');if(Number.isNaN(date.getTime()))return '';
  return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
}
function autopilotStatusClass(status){return status==='scheduled'?'ok':status==='analysis_failed'?'warn':status==='analyzing'?'warn':'';}
function updateAutopilotProgress(current,total,title,detail){
  const percent=total?Math.min(100,current/total*100):0;$('autopilotProgress').classList.remove('hidden');
  $('autopilotProgressTitle').textContent=title;$('autopilotProgressPercent').textContent=`${percent.toLocaleString('fr-FR',{maximumFractionDigits:0})} %`;
  $('autopilotProgressBar').value=percent;$('autopilotProgressDetail').textContent=detail;
}
function hideAutopilotProgress(){$('autopilotProgress').classList.add('hidden');}

async function loadAutopilotQueue({preserveNotice=false}={}){
  const root=$('autopilotQueue');root.innerHTML='<p class="muted">Chargement de la file…</p>';if(!preserveNotice)hideNotice('autopilotNotice');
  try{
    const data=await api('/api/autopilot/queue');autopilotItems=data.items||[];
    try{if(!localStorage.getItem('igstudio.autopilotFrequency'))$('autopilotPostsPerWeek').value=String(data.default_posts_per_week||3);}catch{}
    const active=autopilotItems.filter(item=>item.status!=='scheduled').length;$('autopilotCount').textContent=active;
    renderAutopilotQueue();
  }catch(err){autopilotItems=[];$('autopilotCount').textContent='0';root.innerHTML='';setNotice('autopilotNotice',err.message,'error');}
}
function renderAutopilotQueue(){
  const root=$('autopilotQueue');root.innerHTML='';
  if(!autopilotItems.length){root.innerHTML='<div class="empty-state">Aucun contenu dans Auto-pilot. Prépare une publication puis clique sur « Mettre dans Auto-pilot ».</div>';return;}
  for(const item of autopilotItems){
    const card=document.createElement('article');card.className=`autopilot-card status-${item.status||'queued'}`;
    card.innerHTML='<div class="autopilot-preview"></div><div class="autopilot-card-body"><div class="autopilot-card-heading"><div><strong class="title"></strong><p class="meta"></p></div><span class="pill status"></span></div><div class="autopilot-analysis hidden"><strong>Analyse visuelle</strong><p class="summary"></p><div class="tags"></div></div><div class="autopilot-proposal hidden"><label>Horaire proposé<input class="proposed-date" type="datetime-local"></label><p class="reason"></p></div><p class="error-text"></p><div class="autopilot-actions"></div></div>';
    card.querySelector('.title').textContent=item.title||'Publication Instagram';
    const typeLabel={reel:'Reel',photo:'Photo',carousel:'Carrousel',story:'Story'}[item.media_kind]||item.media_kind;
    const options=[typeLabel,item.location,item.device,item.workflow==='manual_music'?'Musique à finaliser':null,item.mute_audio?'Son coupé':null].filter(Boolean);
    card.querySelector('.meta').textContent=options.join(' • ');
    const status=card.querySelector('.status');status.textContent=autopilotStatusLabels[item.status]||item.status||'Inconnu';const statusClass=autopilotStatusClass(item.status);if(statusClass)status.classList.add(statusClass);
    const firstMedia=(item.media_items||[])[0];if(firstMedia?.url){
      const preview=firstMedia.media_type==='image'?document.createElement('img'):document.createElement('video');preview.src=firstMedia.url;preview.alt=item.title||'Aperçu Auto-pilot';
      if(preview.tagName==='VIDEO'){preview.controls=true;preview.muted=true;preview.playsInline=true;preview.preload='metadata';}
      card.querySelector('.autopilot-preview').appendChild(preview);
    }
    if(item.visual_analysis){
      const analysis=card.querySelector('.autopilot-analysis');analysis.classList.remove('hidden');analysis.querySelector('.summary').textContent=item.visual_analysis.summary||'Analyse terminée.';
      const tags=analysis.querySelector('.tags');for(const value of (item.visual_analysis.tags||[]).slice(0,6)){const tag=document.createElement('span');tag.className='pill';tag.textContent=value;tags.appendChild(tag);}
    }
    if(item.proposal?.scheduled_for){
      const proposal=card.querySelector('.autopilot-proposal');proposal.classList.remove('hidden');proposal.querySelector('.proposed-date').value=autopilotDateInputValue(item.proposal.scheduled_for);proposal.querySelector('.proposed-date').disabled=item.status==='scheduled';
      proposal.querySelector('.reason').textContent=`${item.proposal.reason||'Créneau proposé.'}${item.proposal.confidence!==undefined?` • confiance ${item.proposal.confidence} %`:''}`;
    }
    if(item.last_error)card.querySelector('.error-text').textContent=item.last_error;
    const actions=card.querySelector('.autopilot-actions');
    if(['queued','analysis_failed'].includes(item.status)){
      const analyze=document.createElement('button');analyze.className='secondary';analyze.type='button';analyze.textContent=item.status==='analysis_failed'?'Relancer l’analyse':'Analyser ce média';analyze.onclick=()=>analyzeSingleAutopilotItem(item,analyze);actions.appendChild(analyze);
    }
    if(item.status==='planned'){
      const approve=document.createElement('button');approve.className='primary';approve.type='button';approve.textContent='Valider et programmer';approve.onclick=()=>approveAutopilotItem(item,card,approve);actions.appendChild(approve);
    }
    if(item.status==='scheduled'){
      const calendar=document.createElement('button');calendar.className='secondary';calendar.type='button';calendar.textContent='Voir dans le calendrier';calendar.onclick=()=>activateTab('calendar');actions.appendChild(calendar);
    }else{
      const remove=document.createElement('button');remove.className='ghost';remove.type='button';remove.textContent='Retirer de la file';remove.onclick=()=>removeAutopilotItem(item);actions.appendChild(remove);
    }
    root.appendChild(card);
  }
}
async function analyzeSingleAutopilotItem(item,button){
  button.disabled=true;button.textContent='Extraction des images…';hideNotice('autopilotNotice');
  try{await api(`/api/autopilot/queue/${item.id}/analyze`,{method:'POST'});await loadAutopilotQueue({preserveNotice:true});setNotice('autopilotNotice',`« ${item.title||'Média'} » analysé avec Groq Vision.`,'success');}
  catch(err){await loadAutopilotQueue({preserveNotice:true});setNotice('autopilotNotice',err.message,'error');}
}
async function removeAutopilotItem(item){
  if(!confirm(`Retirer « ${item.title||'ce contenu'} » de la file ? Le média restera dans la bibliothèque.`))return;
  try{await api(`/api/autopilot/queue/${item.id}`,{method:'DELETE'});await loadAutopilotQueue();}
  catch(err){setNotice('autopilotNotice',err.message,'error');}
}
async function approveAutopilotItem(item,card,button){
  const value=card.querySelector('.proposed-date').value;if(!value){setNotice('autopilotNotice','Choisis une date et une heure.','error');return;}
  if(!confirm(`Programmer « ${item.title||'cette publication'} » le ${new Date(value).toLocaleString('fr-FR')} ?`))return;
  button.disabled=true;button.textContent='Programmation…';
  try{await api(`/api/autopilot/queue/${item.id}/approve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scheduled_for:new Date(value).toISOString()})});playStudioChime();await loadAutopilotQueue({preserveNotice:true});setNotice('autopilotNotice','Publication validée et ajoutée au calendrier ✅','success');}
  catch(err){setNotice('autopilotNotice',err.message,'error');button.disabled=false;button.textContent='Valider et programmer';}
}
async function analyzeAndPlanAutopilot(){
  const button=$('autopilotAnalyzeBtn');button.disabled=true;button.textContent='Analyse en cours…';hideNotice('autopilotNotice');hideNotice('autopilotPlanSummary');
  try{
    await loadAutopilotQueue();const pending=autopilotItems.filter(item=>['queued','analysis_failed'].includes(item.status));const total=Math.max(1,pending.length+1);let completed=0,failures=[];
    for(const item of pending){
      updateAutopilotProgress(completed,total,'Analyse visuelle de la file',`${completed+1}/${pending.length} • ${item.title||'Publication'}`);
      try{await api(`/api/autopilot/queue/${item.id}/analyze`,{method:'POST'});}catch(err){failures.push(`${item.title||'Média'} : ${err.message}`);}
      completed+=1;updateAutopilotProgress(completed,total,'Analyse visuelle de la file',`${completed}/${pending.length} média(s) traité(s)`);
    }
    updateAutopilotProgress(completed,total,'Création du planning','Groq compare les médias aux meilleurs créneaux observés…');
    const data=await api('/api/autopilot/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({posts_per_week:Number($('autopilotPostsPerWeek').value)||3,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone||'Europe/Paris'})});
    updateAutopilotProgress(total,total,'Planning terminé',`${data.plan.items.length} proposition(s) prête(s) à valider`);
    await loadAutopilotQueue({preserveNotice:true});
    setNotice('autopilotPlanSummary',data.plan.summary||'Planning Auto-pilot prêt.','success');
    if(failures.length)setNotice('autopilotNotice',`${failures.length} média(s) non analysé(s) :\n${failures.join('\n')}`,'error');else setNotice('autopilotNotice','Analyse terminée. Vérifie puis valide chaque proposition.','success');
  }catch(err){setNotice('autopilotNotice',err.message,'error');}
  finally{button.disabled=false;button.textContent='✨ Analyser et proposer les horaires';setTimeout(hideAutopilotProgress,1200);}
}
$('autopilotAnalyzeBtn').addEventListener('click',analyzeAndPlanAutopilot);
$('refreshAutopilot').addEventListener('click',loadAutopilotQueue);
$('autopilotPostsPerWeek').addEventListener('change',()=>{try{localStorage.setItem('igstudio.autopilotFrequency',$('autopilotPostsPerWeek').value);}catch{}});
try{const savedAutopilotFrequency=localStorage.getItem('igstudio.autopilotFrequency');if(['1','2','3','4','5','6','7'].includes(savedAutopilotFrequency))$('autopilotPostsPerWeek').value=savedAutopilotFrequency;}catch{}

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
    const warnings=[],critical=[];
    const accountStorage=usage.account_storage_bytes;
    const displayedStorage=accountStorage===null||accountStorage===undefined?usage.bucket_storage_bytes:accountStorage;
    const storageRemaining=renderR2Quota('r2Storage',displayedStorage,usage.free_storage_bytes||10000000000,formatR2Storage);
    $('r2StorageRemaining').textContent=`${formatR2Storage(storageRemaining)} avant 10 Go • uploads bloqués à ${formatR2Storage(usage.studio_storage_limit_bytes||9000000000)}`;
    const storagePercent=Number(displayedStorage)/(Number(usage.free_storage_bytes)||10000000000)*100;
    if(storagePercent>=90)critical.push('Le stockage R2 dépasse 90 % du quota gratuit.');
    else if(storagePercent>=80)warnings.push('Le stockage R2 dépasse 80 % du quota gratuit.');
    if(usage.billing_ready||usage.analytics_ready&&usage.billing_period_ready){
      const remainingA=renderR2Quota('r2ClassA',usage.class_a?.used,usage.class_a?.limit||1000000,formatStat);
      const remainingB=renderR2Quota('r2ClassB',usage.class_b?.used,usage.class_b?.limit||10000000,formatStat);
      const sourceSuffix=usage.billing_authoritative?'facturation Cloudflare':'Analytics aligné au cycle';
      $('r2ClassARemaining').textContent=`${formatStat(remainingA)} restantes • ${sourceSuffix}`;
      $('r2ClassBRemaining').textContent=`${formatStat(remainingB)} restantes • ${sourceSuffix}`;
      const classAPercent=Number(usage.class_a?.percent)||0,classBPercent=Number(usage.class_b?.percent)||0;
      if(classAPercent>=90)critical.push('Les opérations de classe A dépassent 90 % du quota gratuit.');
      else if(classAPercent>=80)warnings.push('Les opérations de classe A dépassent 80 % du quota gratuit.');
      if(classBPercent>=90)critical.push('Les opérations de classe B dépassent 90 % du quota gratuit.');
      else if(classBPercent>=80)warnings.push('Les opérations de classe B dépassent 80 % du quota gratuit.');
      const unknown=Number(usage.unknown_operations)||0;
      if(unknown){
        const details=(usage.unknown_operation_types||[]).map(item=>`${item.action} (${formatStat(item.requests)})`).join(', ');
        warnings.push(`${formatStat(unknown)} opération(s) non classée(s)${details?` : ${details}`:''}. Elles ne sont ajoutées ni à A ni à B tant que Cloudflare ne documente pas leur catégorie.`);
      }
    }else{
      renderR2Quota('r2ClassA',0,1000000,formatStat);renderR2Quota('r2ClassB',0,10000000,formatStat);
      $('r2ClassAValue').textContent='Facturation non vérifiée';$('r2ClassBValue').textContent='Facturation non vérifiée';
      $('r2ClassARemaining').textContent='Ajoute CLOUDFLARE_BILLING_API_TOKEN';$('r2ClassBRemaining').textContent='Permission Account Billing — Read';
      setNotice('r2UsageNotice',usage.billing_error||usage.analytics_error||'Le Studio refuse d’afficher une estimation comme une valeur facturable. Ajoute le token Billing Cloudflare.','error');
    }
    if(usage.bucket_storage_error)critical.push(usage.bucket_storage_error);
    if(usage.analytics_error)critical.push(usage.analytics_error);
    if((usage.billing_ready||usage.analytics_ready)&&(critical.length||warnings.length)){
      const messages=[...critical,...warnings];
      setNotice('r2UsageNotice',messages.join('\n'),critical.length?'error':'warning');
    }
    const start=usage.period_start?statsDate(usage.period_start):'inconnue',end=usage.period_end?statsDate(usage.period_end):'inconnue';
    const source=usage.billing_authoritative?'API Billing Cloudflare (valeurs de facturation)':usage.billing_period_ready?'Analytics R2 sur la période de facturation':'source de facturation indisponible';
    const cost=usage.billed_cost===null||usage.billed_cost===undefined?'':` • coût facturé relevé : ${Number(usage.billed_cost).toLocaleString('fr-FR',{style:'currency',currency:usage.billing_currency||'USD'})}`;
    $('r2UsageMeta').textContent=`Cycle Cloudflare : ${start} → ${end} • Source : ${source}${cost}${usage.bucket_name?` • Bucket Studio « ${usage.bucket_name} » : ${formatR2Storage(usage.bucket_storage_bytes)}`:''} • stockage affiché : ${accountStorage===null||accountStorage===undefined?'mesure exacte du bucket Studio':'dernier relevé du compte R2'}.`;
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
        if(itemType==='image'&&item.text_editor){
          mediaItem.text_editor=imageEditorCleanState(item.text_editor);mediaItem.edited=true;
          mediaItem.original_url=item.source_url||item.secure_url;mediaItem.original_library_id=item.source_library_id||item.id;mediaItem.original_thumbnail_url=item.source_url||item.thumbnail_url||item.secure_url;
        }
        const selectedKind=$('mediaKind').value;
        const matchesCarousel=(itemType==='image'&&selectedKind==='carousel')||(itemType==='video'&&selectedKind==='carousel_video');
        const matchesStory=(itemType==='image'&&selectedKind==='story_photo')||(itemType==='video'&&selectedKind==='story_video');
        if(matchesCarousel){
          if(selectedMediaItems.length>=10){setNotice('libraryNotice','Le carrousel contient déjà 10 médias.','error');return;}
          selectedMediaItems.push(mediaItem);
        }else if(matchesStory){
          selectedMediaItems=[mediaItem];
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
  if(calendarView==='list'&&calendarListRange==='today'){const start=new Date(calendarCursor);start.setHours(0,0,0,0);const end=new Date(start);end.setDate(end.getDate()+1);return {start,end};}
  if(calendarView==='week'||(calendarView==='list'&&calendarListRange==='week')){const start=startOfWeek(calendarCursor);const end=new Date(start);end.setDate(end.getDate()+7);return {start,end};}
  const start=new Date(calendarCursor.getFullYear(),calendarCursor.getMonth(),1);const end=new Date(calendarCursor.getFullYear(),calendarCursor.getMonth()+1,1);return {start,end};
}
function eventDate(item){return new Date(item.scheduled_for||item.published_at||item.created_at);}
function localDateKey(date){return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;}
function calendarTitle(start,end){
  if(calendarView==='list'&&calendarListRange==='today')return start.toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long',year:'numeric'});
  if(calendarView==='week'||(calendarView==='list'&&calendarListRange==='week')){
    const last=new Date(end);last.setDate(last.getDate()-1);
    return `${start.toLocaleDateString('fr-FR',{day:'numeric',month:'short'})} – ${last.toLocaleDateString('fr-FR',{day:'numeric',month:'short',year:'numeric'})}`;
  }
  return new Intl.DateTimeFormat('fr-FR',{month:'long',year:'numeric'}).format(start);
}
function calendarListHeading(){
  if(calendarView==='week'||(calendarView==='list'&&calendarListRange==='week'))return 'Publications de la semaine';
  if(calendarView==='list'&&calendarListRange==='today')return 'Publications d’aujourd’hui';
  return 'Publications du mois';
}
function calendarEmptyPeriod(){
  if(calendarView==='week'||(calendarView==='list'&&calendarListRange==='week'))return 'cette semaine';
  if(calendarView==='list'&&calendarListRange==='today')return 'aujourd’hui';
  return 'ce mois-ci';
}
function resetCalendarCursor(){calendarCursor=new Date();if(calendarView==='month'||(calendarView==='list'&&calendarListRange==='month'))calendarCursor.setDate(1);}
function shiftCalendarCursor(direction){
  if(calendarView==='week'||(calendarView==='list'&&calendarListRange==='week'))calendarCursor.setDate(calendarCursor.getDate()+(direction*7));
  else if(calendarView==='list'&&calendarListRange==='today')calendarCursor.setDate(calendarCursor.getDate()+direction);
  else calendarCursor.setMonth(calendarCursor.getMonth()+direction);
}
async function loadCalendar(){
  if(calendarLoadPromise)return calendarLoadPromise;
  const requestedVersion=calendarEventVersion;
  calendarLoadPromise=(async()=>{
    const {start,end}=calendarBounds();$('calendarTitle').textContent=calendarTitle(start,end);hideNotice('calendarNotice');
    document.querySelectorAll('[data-calendar-view]').forEach(button=>{const active=button.dataset.calendarView===calendarView;button.className=active?'secondary active':'ghost';});
    const listOnly=calendarView==='list';$('calendarListRange').classList.toggle('hidden',!listOnly);
    document.querySelectorAll('[data-calendar-list-range]').forEach(button=>{const active=button.dataset.calendarListRange===calendarListRange;button.className=active?'secondary active':'ghost';});
    document.querySelector('.calendar-weekdays').classList.toggle('hidden',listOnly);$('calendarGrid').classList.toggle('hidden',listOnly);$('calendarDragHelp').classList.toggle('hidden',listOnly);
    $('calendarListTitle').textContent=calendarListHeading();
    try{const data=await api(`/api/publications/calendar?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`);renderCalendar(data.items,start,end);}
    catch(err){setNotice('calendarNotice',err.message,'error');$('calendarGrid').innerHTML='';$('calendarList').innerHTML='';}
    finally{calendarRenderedVersion=Math.max(calendarRenderedVersion,requestedVersion);}
  })();
  try{return await calendarLoadPromise;}
  finally{
    calendarLoadPromise=null;
    if(calendarEventVersion>calendarRenderedVersion)scheduleRealtimeCalendarRefresh();
  }
}

function calendarPanelIsActive(){return $('calendar')?.classList.contains('active');}
function setCalendarLiveStatus(state,text){const status=$('calendarLiveStatus');if(!status)return;status.className=`calendar-live-status ${state}`;status.lastChild.textContent=` ${text}`;}
function scheduleRealtimeCalendarRefresh(){
  if(!calendarPanelIsActive()||studioSessionExpired)return;
  clearTimeout(calendarRealtimeTimer);
  calendarRealtimeTimer=setTimeout(()=>loadCalendar(),180);
}
function markCalendarChanged(){calendarEventVersion+=1;scheduleRealtimeCalendarRefresh();}
function disconnectStudioRealtime(){
  clearTimeout(calendarRealtimeTimer);
  if(studioRealtimeSource){studioRealtimeSource.close();studioRealtimeSource=null;}
}
function connectStudioRealtime(){
  if(studioSessionExpired||document.visibilityState!=='visible'||studioRealtimeSource||!('EventSource' in window)){
    if(!('EventSource' in window))setCalendarLiveStatus('offline','Actualisation à l’ouverture');
    return;
  }
  setCalendarLiveStatus('connecting','Connexion en direct…');
  const source=new EventSource('/api/events');studioRealtimeSource=source;
  let receivedReady=false;
  source.onopen=()=>setCalendarLiveStatus('live','Synchronisé en direct');
  source.addEventListener('ready',()=>{setCalendarLiveStatus('live','Synchronisé en direct');if(receivedReady)markCalendarChanged();receivedReady=true;});
  source.addEventListener('calendar',()=>markCalendarChanged());
  source.onerror=()=>{
    if(studioSessionExpired){source.close();return;}
    setCalendarLiveStatus('offline','Reconnexion automatique…');
  };
}
function sameLocalDay(first,second){return first.getFullYear()===second.getFullYear()&&first.getMonth()===second.getMonth()&&first.getDate()===second.getDate();}
function calendarDayCell(date,items){
  const cell=document.createElement('div');cell.className='calendar-day';cell.dataset.date=localDateKey(date);cell.innerHTML='<span class="day-number"></span><div class="day-events"></div>';
  cell.querySelector('.day-number').textContent=calendarView==='week'?date.toLocaleDateString('fr-FR',{weekday:'short',day:'numeric'}):date.getDate();
  if(sameLocalDay(date,new Date()))cell.classList.add('today');
  installCalendarDropTarget(cell);
  for(const item of items.filter(value=>sameLocalDay(eventDate(value),date))){
    const badge=document.createElement('button');badge.className=`calendar-event status-${item.status} kind-${item.media_kind||'reel'}`;badge.textContent=`${item.media_kind==='story'?'Story • ':''}${item.title||statusLabels[item.status]}`;badge.title=`${item.media_kind==='story'?'Story • ':''}${statusLabels[item.status]||item.status} • ${eventDate(item).toLocaleString()}`;
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
  if(!items.length){list.innerHTML=`<p class="muted">Aucune publication ${calendarEmptyPeriod()}.</p>`;return;}
  for(const item of [...items].sort((a,b)=>eventDate(a)-eventDate(b))){
    const statusKey=Object.prototype.hasOwnProperty.call(statusLabels,item.status)?item.status:'unknown';
    const row=document.createElement('article');row.className=`publication-item status-${statusKey}`;row.id=`publication-${item.id}`;
    row.innerHTML='<div><div class="publication-item-heading"><strong class="title"></strong><span class="publication-status"></span></div><p class="muted small details"></p><p class="error-text"></p></div><div class="publication-actions"></div>';
    const mediaLabel={photo:'Photo',carousel:'Carrousel',reel:'Reel',story:'Story'}[item.media_kind||'reel'];
    row.querySelector('.title').textContent=item.title||'Publication Instagram';row.querySelector('.details').textContent=`${eventDate(item).toLocaleString('fr-FR')} • ${mediaLabel}${item.publication_mode==='trial'?' • Trial Reel':''}${item.workflow==='manual_music'?' • Musique manuelle':''}`;
    const statusBadge=row.querySelector('.publication-status');statusBadge.className=`publication-status status-${statusKey}`;statusBadge.textContent=statusLabels[item.status]||item.status||'Statut inconnu';
    if(item.last_error)row.querySelector('.error-text').textContent=item.last_error;
    if(['scheduled','failed','awaiting_manual'].includes(item.status)){const cancel=document.createElement('button');cancel.className='ghost';cancel.textContent='Annuler';cancel.onclick=async()=>{if(!confirm('Annuler cette publication ?'))return;try{await api(`/api/publications/${item.id}`,{method:'DELETE'});loadCalendar();}catch(err){setNotice('calendarNotice',err.message,'error');}};row.querySelector('.publication-actions').appendChild(cancel);}
    if(item.status==='awaiting_manual'){
      const prepare=document.createElement('button');prepare.className='primary';prepare.textContent='Préparer les médias';prepare.onclick=async()=>{prepare.disabled=true;prepare.textContent='Préparation…';activateTab('composer');await prepareInstagramFinalization(item);prepare.disabled=false;prepare.textContent='Préparer les médias';};row.querySelector('.publication-actions').appendChild(prepare);
      const copy=document.createElement('button');copy.className='secondary';copy.textContent='Copier le texte';copy.onclick=()=>copyInstagramCaption(item.caption||'','calendarNotice');row.querySelector('.publication-actions').appendChild(copy);
    }
    list.appendChild(row);
  }
}
$('calendarPrev').addEventListener('click',()=>{shiftCalendarCursor(-1);loadCalendar();});
$('calendarNext').addEventListener('click',()=>{shiftCalendarCursor(1);loadCalendar();});
$('calendarToday').addEventListener('click',()=>{resetCalendarCursor();loadCalendar();});
document.querySelectorAll('[data-calendar-view]').forEach(button=>button.addEventListener('click',()=>{calendarView=button.dataset.calendarView;resetCalendarCursor();try{localStorage.setItem('igstudio.calendarView',calendarView);}catch{}loadCalendar();}));
document.querySelectorAll('[data-calendar-list-range]').forEach(button=>button.addEventListener('click',()=>{calendarListRange=button.dataset.calendarListRange;resetCalendarCursor();try{localStorage.setItem('igstudio.calendarListRange',calendarListRange);}catch{}loadCalendar();}));

function formatStat(value){return new Intl.NumberFormat('fr-FR',{notation:Number(value)>=10000?'compact':'standard',maximumFractionDigits:1}).format(Number(value)||0);}
function formatOptionalStat(value,available=true){return available&&value!==null&&value!==undefined?formatStat(value):'—';}
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
  return items.sort(comparators[mode]||comparators.date_desc);
}
function renderAnalyticsPosts(){
  const posts=$('statsPosts');posts.innerHTML='';
  for(const item of sortedAnalyticsPosts()){
    const row=document.createElement('article');row.className='stats-post';row.innerHTML='<div class="stats-post-main"><span class="pill kind"></span><strong class="hook"></strong><span class="date"></span></div><div class="stats-post-metrics"><span class="views"></span><span class="likes"></span><span class="reach"></span><span class="rate"></span><span class="delta"></span></div><a class="ghost permalink" target="_blank" rel="noopener">Voir</a><details class="stats-post-details"><summary>Plus de statistiques</summary><div class="stats-post-detail-grid"></div></details>';
    row.querySelector('.kind').textContent={reel:'Reel',photo:'Photo',carousel:'Carrousel',story:'Story'}[item.media_kind]||'Post';
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
function renderContentIdeas(report,createdAt='',savedBrief=''){
  const root=$('contentIdeasReport');root.innerHTML='';
  if(savedBrief&&!$('contentIdeasBrief').value.trim())$('contentIdeasBrief').value=savedBrief;
  if(!report||!Array.isArray(report.ideas)||!report.ideas.length){
    root.innerHTML='<p class="muted">Synchronise au moins 3 publications puis génère tes premiers concepts.</p>';
    $('contentIdeasMeta').textContent='';return;
  }
  if(report.diagnosis){const diagnosis=document.createElement('p');diagnosis.className='content-ideas-diagnosis';diagnosis.textContent=report.diagnosis;root.appendChild(diagnosis);}
  report.ideas.slice(0,3).forEach((idea,index)=>{
    const card=document.createElement('article');card.className='content-idea-card';
    card.innerHTML='<header><div><span class="idea-number"></span><h3></h3></div><span class="pill objective"></span></header><p class="concept"></p><p class="why"></p><div class="idea-key-facts"><span class="duration"></span><span class="equipment"></span></div><section class="idea-hook"><span>Hook • 0–2 s</span><strong></strong></section><section><h4>Protocole de tournage</h4><ol class="shots"></ol></section><section class="screen-text-section"><h4>Textes à l’écran</h4><div class="screen-texts"></div></section><div class="idea-finish"><section><h4>CTA naturel</h4><p class="cta"></p></section><section><h4>Angle de caption</h4><p class="caption-angle"></p></section></div><section class="success-metric"><h4>À mesurer</h4><p></p></section>';
    card.querySelector('.idea-number').textContent=`IDÉE ${index+1}`;
    card.querySelector('h3').textContent=idea.title||`Concept ${index+1}`;
    card.querySelector('.objective').textContent=idea.objective||'Test de croissance';
    card.querySelector('.concept').textContent=idea.concept||'';if(!idea.concept)card.querySelector('.concept').classList.add('hidden');
    card.querySelector('.why').textContent=idea.why_from_stats?`Pourquoi la tester : ${idea.why_from_stats}`:'';if(!idea.why_from_stats)card.querySelector('.why').classList.add('hidden');
    const duration=Number(idea.duration_seconds)||0;card.querySelector('.duration').textContent=duration?`⏱ ${duration} s`:'';if(!duration)card.querySelector('.duration').classList.add('hidden');
    const equipment=idea.equipment&&idea.equipment!=='Matériel indiqué dans le brief'?idea.equipment:'';card.querySelector('.equipment').textContent=equipment?`🎥 ${equipment}`:'';if(!equipment)card.querySelector('.equipment').classList.add('hidden');
    if(!duration&&!equipment)card.querySelector('.idea-key-facts').classList.add('hidden');
    card.querySelector('.idea-hook strong').textContent=idea.hook||'Hook à préciser';
    for(const shot of idea.shots||[]){const item=document.createElement('li');item.textContent=shot;card.querySelector('.shots').appendChild(item);}
    for(const value of idea.on_screen_text||[]){const chip=document.createElement('span');chip.textContent=value;card.querySelector('.screen-texts').appendChild(chip);}
    if(!(idea.on_screen_text||[]).length)card.querySelector('.screen-text-section').classList.add('hidden');
    card.querySelector('.cta').textContent=idea.cta||'';
    card.querySelector('.caption-angle').textContent=idea.caption_angle||'';if(!idea.caption_angle){card.querySelector('.caption-angle').closest('section').classList.add('hidden');card.querySelector('.idea-finish').classList.add('single');}
    card.querySelector('.success-metric p').textContent=idea.success_metric||'';
    root.appendChild(card);
  });
  $('contentIdeasMeta').textContent=createdAt?`Protocoles enregistrés dans le Studio • ${new Date(createdAt).toLocaleString('fr-FR')}`:'Protocoles enregistrés dans le Studio.';
}
function renderAnalytics(data){
  const summary=data.summary||{};
  const account=data.account||{},profile=account.profile||{},period=account.period||{},accountMetrics=period.metrics||{};
  const available=new Set(Array.isArray(period.available_metrics)?period.available_metrics:[]);
  const exact=new Set(Array.isArray(period.exact_metrics)?period.exact_metrics:period.available_metrics||[]);
  const metric=(name)=>formatOptionalStat(accountMetrics[name],available.has(name)&&exact.has(name));
  $('statsProfileMediaCount').textContent=formatOptionalStat(profile.media_count,profile.media_count!==null&&profile.media_count!==undefined);
  $('statsFollowers').textContent=formatOptionalStat(profile.followers_count,profile.followers_count!==null&&profile.followers_count!==undefined);
  $('statsFollowing').textContent=profile.follows_count===null||profile.follows_count===undefined?'Abonnements indisponibles':`${formatStat(profile.follows_count)} abonnement(s)`;
  $('statsAnalyzedCount').textContent=`${formatStat(summary.media_count)} média(s) analysé(s)`;
  $('statsViews').textContent=metric('views');
  $('statsReach').textContent=metric('reach');
  $('statsInteractions').textContent=metric('total_interactions');
  $('statsEngagement').textContent=exact.has('total_interactions')&&(exact.has('reach')||exact.has('views'))?`Interactions / ${exact.has('reach')?'portée':'vues'} : ${Number(period.engagement_rate||0).toFixed(1)} %`:'Taux indisponible';
  $('statsNetFollowers').textContent=metric('net_follows');
  $('statsNetFollowers').classList.toggle('negative',available.has('net_follows')&&Number(accountMetrics.net_follows)<0);
  $('statsNetFollowers').classList.toggle('positive',available.has('net_follows')&&Number(accountMetrics.net_follows)>0);
  $('statsFollowerMovement').textContent=available.has('follows')&&available.has('unfollows')?`+${formatStat(accountMetrics.follows)} • −${formatStat(accountMetrics.unfollows)}`:'Gains et pertes indisponibles';
  $('statsViewsHelp').textContent=Number(period.chunk_count)>1?`Somme exacte de ${period.chunk_count} fenêtres Meta`:`Source Meta globale • ${period.days||statsPeriodValue()} jours`;
  $('statsReachHelp').textContent=exact.has('reach')?'Comptes uniques selon Meta':'Indisponible exactement sur cette période — utilise 30 jours';
  const accountNotes=Array.isArray(period.notes)?period.notes:[];
  $('statsOfficialMeta').textContent=period.start&&period.end?`Compte @${profile.username||'Instagram'} • chiffres Meta du ${statsDate(period.display_start||period.start)} au ${statsDate(period.display_end||period.end)}${(period.errors||[]).length?' • certaines métriques sont indisponibles':''}${accountNotes.length?` • ${accountNotes.join(' ')}`:''}`:`Compte @${profile.username||'Instagram'} • synchronise pour charger les Insights globaux officiels.`;
  const details=$('statsAccountDetails');details.innerHTML='';
  const detailMetrics=[['Comptes engagés','accounts_engaged'],["J’aime",'likes'],['Commentaires','comments'],['Partages','shares'],['Enregistrements','saves'],['Réponses','replies']];
  for(const [label,key] of detailMetrics){
    if(!available.has(key)||!exact.has(key))continue;
    const item=document.createElement('article');item.innerHTML='<span></span><strong></strong>';item.querySelector('span').textContent=label;item.querySelector('strong').textContent=formatStat(accountMetrics[key]);details.appendChild(item);
  }
  if(!details.children.length)details.innerHTML='<p class="muted small">Les statistiques détaillées du compte apparaîtront après la prochaine synchronisation.</p>';
  $('statsMediaCount').textContent=formatStat(summary.media_count);
  $('statsMediaKinds').textContent=`${summary.reels||0} Reel(s) • ${summary.photos||0} photo(s) • ${summary.carousels||0} carrousel(s)`;
  $('statsMediaViews').textContent=formatStat(summary.views);
  $('statsMediaReach').textContent=formatStat(summary.reach);
  $('statsMediaEngagement').textContent=`${Number(summary.engagement_rate||0).toFixed(1)} %`;
  $('statsMediaInteractions').textContent=`${formatStat(summary.interactions)} interaction(s)`;
  const sync=data.sync||{};
  $('statsSyncMeta').textContent=sync.last_synced_at?`Dernier relevé : ${new Date(sync.last_synced_at).toLocaleString('fr-FR')} • ${sync.metrics_updated||0} média(s) détaillé(s) mis à jour • ${profile.media_count??'—'} publication(s) sur le profil`:'Aucune synchronisation enregistrée.';
  if(sync.permission_required&&sync.last_error)setNotice('statsNotice',sync.last_error,'error');
  else if(sync.last_synced_at&&!available.size)setNotice('statsNotice','Les médias ont été synchronisés, mais Meta n’a pas renvoyé les Insights globaux du compte. Vérifie la permission instagram_business_manage_insights puis reconnecte Instagram.','warning');
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
  renderContentIdeas(data.content_ideas,data.content_ideas_created_at,data.content_ideas_brief);
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
$('generateContentIdeasBtn').addEventListener('click',async()=>{
  const brief=$('contentIdeasBrief').value.trim();
  const button=$('generateContentIdeasBtn');button.disabled=true;button.textContent='Création des protocoles…';hideNotice('contentIdeasNotice');
  try{
    const data=await api(`/api/analytics/content-ideas?period_days=${statsPeriodValue()}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({brief})});
    renderContentIdeas(data.report,new Date().toISOString(),brief);
    setNotice('contentIdeasNotice',`3 protocoles créés avec ${data.model} et enregistrés dans MongoDB.`,'success');
  }catch(err){setNotice('contentIdeasNotice',err.message,'error');}
  finally{button.disabled=false;button.textContent='Créer 3 protocoles de vidéos';}
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
window.addEventListener('beforeunload',()=>studioRealtimeSource?.close());
