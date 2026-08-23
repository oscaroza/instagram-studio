(function(){
  const button=document.getElementById('passkeyLoginBtn');
  const notice=document.getElementById('passkeyLoginNotice');
  if(!button)return;
  if(!window.Passkeys?.supported()){
    button.classList.add('hidden');
    return;
  }
  async function request(url,body={}){
    const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    let data={};try{data=await response.json();}catch{throw new Error('Réponse serveur invalide.');}
    if(!data.ok)throw new Error(data.error||'Connexion impossible.');
    return data;
  }
  button.addEventListener('click',async()=>{
    button.disabled=true;button.textContent='Confirmation Face ID…';notice.className='notice hidden';
    try{
      const options=await request('/api/passkeys/authenticate/options');
      const credential=await window.Passkeys.get(options.public_key);
      const result=await request('/api/passkeys/authenticate/verify',{ceremony_id:options.ceremony_id,credential,next:button.dataset.next||'/'});
      location.assign(result.next||'/');
    }catch(error){
      notice.textContent=error?.name==='NotAllowedError'?'Face ID/passkey a été annulé ou a expiré.':error.message;
      notice.className='notice error';
    }finally{button.disabled=false;button.textContent='Se connecter avec Face ID / passkey';}
  });
})();
