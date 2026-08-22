const $ = (id) => document.getElementById(id);
const fields = ["videoUrl","description","location","drone","language","tone","extra","caption","hashtags","altText","hook","publicationMode"];

function setNotice(id, text, type="") { const el=$(id); el.textContent=text; el.className=`notice ${type}`; }
function hideNotice(id){ $(id).className="notice hidden"; }

for (const tab of document.querySelectorAll('.tab')) {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
    tab.classList.add('active'); $(tab.dataset.tab).classList.add('active');
    if(tab.dataset.tab==='drafts') renderDrafts();
    if(tab.dataset.tab==='settings') loadPublishingLimit();
  });
}

$('videoFile').addEventListener('change', async (e) => {
  const file=e.target.files[0]; if(!file) return;
  const form=new FormData(); form.append('file',file);
  setNotice('uploadProgress', `Upload de ${file.name}…`);
  try {
    const r=await fetch('/api/upload',{method:'POST',body:form}); const d=await r.json();
    if(!d.ok) throw new Error(d.error||'Upload impossible');
    $('videoUrl').value=d.url;
    setNotice('uploadProgress', `Vidéo prête temporairement (${(d.size/1024/1024).toFixed(1)} Mo).`, 'success');
  } catch(err){ setNotice('uploadProgress',err.message,'error'); }
});

$('generateBtn').addEventListener('click', async () => {
  const btn=$('generateBtn'); btn.disabled=true; btn.textContent='Génération…'; hideNotice('actionMessage');
  try {
    const payload={description:$('description').value,location:$('location').value,drone:$('drone').value,language:$('language').value,tone:$('tone').value,extra:$('extra').value};
    const r=await fetch('/api/ai/caption',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const d=await r.json();
    if(!d.ok) throw new Error(d.error||'Erreur IA');
    const x=d.result; $('caption').value=x.caption||''; $('hashtags').value=(x.hashtags||[]).join(' '); $('altText').value=x.alt_text||''; $('hook').value=x.hook||'';
    $('aiBadge').textContent=`Généré • ${d.model}`; $('aiBadge').className='pill ok';
  } catch(err){ setNotice('actionMessage',err.message,'error'); }
  finally { btn.disabled=false; btn.textContent='✨ Générer avec Groq'; }
});

function currentDraft(){ const d={}; fields.forEach(f=>d[f]=$(f).value); d.savedAt=new Date().toISOString(); d.id=crypto.randomUUID(); return d; }
function drafts(){ try{return JSON.parse(localStorage.getItem('igstudio.drafts')||'[]')}catch{return []} }
function saveDrafts(v){localStorage.setItem('igstudio.drafts',JSON.stringify(v)); updateDraftCount();}
function updateDraftCount(){$('draftCount').textContent=drafts().length;}

$('saveDraftBtn').addEventListener('click',()=>{const list=drafts();list.unshift(currentDraft());saveDrafts(list.slice(0,50));setNotice('actionMessage','Brouillon enregistré sur cet appareil.','success');});
$('clearDraftsBtn').addEventListener('click',()=>{saveDrafts([]);renderDrafts();});
function loadDraft(id){const d=drafts().find(x=>x.id===id);if(!d)return;fields.forEach(f=>{if(d[f]!==undefined)$(f).value=d[f]});document.querySelector('[data-tab="composer"]').click();}
function deleteDraft(id){saveDrafts(drafts().filter(x=>x.id!==id));renderDrafts();}
function renderDrafts(){const list=drafts(),root=$('draftList');root.innerHTML='';if(!list.length){root.innerHTML='<p class="muted">Aucun brouillon.</p>';return;}for(const d of list){const el=document.createElement('div');el.className='draft-item';const title=(d.location||d.description||'Brouillon').slice(0,70);el.innerHTML=`<h3></h3><p></p><div class="draft-actions"><button class="secondary load">Ouvrir</button><button class="ghost del">Supprimer</button></div>`;el.querySelector('h3').textContent=title;el.querySelector('p').textContent=new Date(d.savedAt).toLocaleString();el.querySelector('.load').onclick=()=>loadDraft(d.id);el.querySelector('.del').onclick=()=>deleteDraft(d.id);root.appendChild(el);}}

$('publishBtn').addEventListener('click', async () => {
  const video=$('videoUrl').value.trim(); if(!video){setNotice('actionMessage','Ajoute une vidéo ou une URL avant de publier.','error');return;}
  const caption=[$('caption').value.trim(),$('hashtags').value.trim()].filter(Boolean).join('\n\n');
  if(!confirm('Publier ce Reel maintenant sur Instagram ?')) return;
  const btn=$('publishBtn');btn.disabled=true;btn.textContent='Publication en cours…';setNotice('actionMessage','Instagram prépare la vidéo. Cela peut prendre un moment.');
  try {const r=await fetch('/api/instagram/publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video_url:video,caption,publication_mode:$('publicationMode').value})});const d=await r.json();if(!d.ok)throw new Error(d.error||'Publication impossible');setNotice('actionMessage',`Publié ✅\nMedia ID: ${d.media_id}`,'success');}
  catch(err){setNotice('actionMessage',err.message,'error');}
  finally{btn.disabled=false;btn.textContent='Publier le Reel';}
});

async function loadPublishingLimit(){
  const target=$('publishingLimit');
  target.textContent='Chargement…';
  try {
    const r=await fetch('/api/instagram/publishing-limit');
    const d=await r.json();
    if(!d.ok) throw new Error(d.error||'Compteur indisponible');
    target.textContent=`${d.used} / ${d.total} (${d.remaining} restantes)`;
    target.className='cap-on';
  } catch(err) {
    target.textContent=err.message;
    target.className='cap-off';
  }
}

$('refreshLimitBtn').addEventListener('click',loadPublishingLimit);

updateDraftCount();
