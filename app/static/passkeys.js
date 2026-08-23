(function(){
  function decodeBase64url(value){
    const padding='='.repeat((4-value.length%4)%4);
    const binary=atob((value+padding).replace(/-/g,'+').replace(/_/g,'/'));
    return Uint8Array.from(binary,character=>character.charCodeAt(0)).buffer;
  }
  function encodeBase64url(value){
    const bytes=new Uint8Array(value);let binary='';
    for(const byte of bytes)binary+=String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
  }
  function creationOptions(options){
    const result={...options,challenge:decodeBase64url(options.challenge),user:{...options.user,id:decodeBase64url(options.user.id)}};
    result.excludeCredentials=(options.excludeCredentials||[]).map(item=>({...item,id:decodeBase64url(item.id)}));
    return result;
  }
  function requestOptions(options){
    const result={...options,challenge:decodeBase64url(options.challenge)};
    result.allowCredentials=(options.allowCredentials||[]).map(item=>({...item,id:decodeBase64url(item.id)}));
    return result;
  }
  function serialize(credential){
    const response=credential.response;
    const serialized={
      id:credential.id,
      rawId:encodeBase64url(credential.rawId),
      type:credential.type,
      authenticatorAttachment:credential.authenticatorAttachment||null,
      clientExtensionResults:credential.getClientExtensionResults(),
      response:{clientDataJSON:encodeBase64url(response.clientDataJSON)}
    };
    if(response.attestationObject){
      serialized.response.attestationObject=encodeBase64url(response.attestationObject);
      serialized.response.transports=typeof response.getTransports==='function'?response.getTransports():[];
    }else{
      serialized.response.authenticatorData=encodeBase64url(response.authenticatorData);
      serialized.response.signature=encodeBase64url(response.signature);
      serialized.response.userHandle=response.userHandle?encodeBase64url(response.userHandle):null;
    }
    return serialized;
  }
  window.Passkeys={
    supported:()=>Boolean(window.PublicKeyCredential&&navigator.credentials),
    create:async options=>serialize(await navigator.credentials.create({publicKey:creationOptions(options)})),
    get:async options=>serialize(await navigator.credentials.get({publicKey:requestOptions(options)}))
  };
})();
