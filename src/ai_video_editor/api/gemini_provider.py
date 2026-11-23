"""Gemini REST integration; outputs validated with existing script/plan schemas."""
import base64,json,wave
from io import BytesIO
from urllib import request,error
from ..project import ValidationError
from .provider_settings import get_key, load_registry, CATALOG

MAX_BYTES=50*1024*1024

def request_model(model,parts,generation,*,projects_dir=None):
    key=get_key('gemini')
    if not key:raise ValidationError('Gemini API key missing; configure API Settings')
    allowed = (load_registry(projects_dir)['models'] if projects_dir is not None else CATALOG)
    if not any(model in task_models.get('gemini', []) for task_models in allowed.values()):
        raise ValidationError('Gemini model not allowed')
    url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    req=request.Request(url,data=json.dumps({'contents':[{'role':'user','parts':parts}],'generationConfig':generation},ensure_ascii=False).encode(),headers={'x-goog-api-key':key,'Content-Type':'application/json'},method='POST')
    try:
        with request.urlopen(req,timeout=180) as resp:raw=resp.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:raise ValidationError('Gemini response exceeds size limit')
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError, TypeError) as exc:
            raise ValidationError('Gemini returned invalid API JSON') from exc
        if not isinstance(payload, dict):
            raise ValidationError('Gemini returned a non-object API response')
        return payload
    except error.HTTPError as exc:raise ValidationError(f'Gemini HTTP {exc.code}; check credentials, model or quota') from exc
    except (error.URLError,TimeoutError,OSError) as exc:raise ValidationError(f'Gemini connection failed: {type(exc).__name__}') from exc

def parts_of(payload):
    try:
        options=payload.get('candidates') or []
        parts=options[0]['content']['parts']
        if not isinstance(parts, list): raise ValueError('not a list')
        return parts
    except (TypeError, IndexError, KeyError, AttributeError, ValueError) as exc:
        raise ValidationError('Gemini returned no valid content parts') from exc

def usage_of(payload, model):
    try:
        u = payload.get('usageMetadata') or {}
        a = int(u.get('promptTokenCount') or 0)
        b = int(u.get('candidatesTokenCount') or 0)
        total = int(u.get('totalTokenCount') or a + b)
    except (AttributeError, ValueError, TypeError) as exc:
        raise ValidationError('Gemini returned invalid usage metadata') from exc
    return {'provider':'gemini','model':model,'input_tokens':a,'output_tokens':b,
            'total_tokens':total,'estimated_cost_usd':None}

def generate_text(task,text,model,*,projects_dir=None,instructions_override=None):
    from ..agent.script_generator import _schema,validate_script
    from ..agent.scene_planner import scene_schema,validate_proposal
    allowed=(load_registry(projects_dir)['models'] if projects_dir is not None else CATALOG)
    if model not in allowed.get(task,{}).get('gemini',[]):raise ValidationError('Unsupported Gemini text model')
    instructions=('Write factual video script from brief. Schema: '+json.dumps(_schema()) if task=='script' else 'Make scene plan with motion, media prompts, transitions; video_clip motion static, last transition none / null length. Schema: '+json.dumps(scene_schema()))
    if instructions_override is not None:
        from ..agent.scene_planner import scene_instructions
        if task != 'scene_plan': raise ValidationError('Custom instructions only supported for scene planning')
        instructions = scene_instructions(instructions_override)
    result=request_model(model,[{'text':instructions+'\nINPUT:\n'+text}],{'responseMimeType':'application/json'},projects_dir=projects_dir)
    try:content=json.loads(next(p['text'] for p in parts_of(result) if 'text' in p))
    except (ValueError,TypeError,StopIteration,KeyError,AttributeError) as exc:raise ValidationError('Gemini returned invalid structured JSON') from exc
    if task=='script':validate_script(content)
    elif task=='scene_plan':validate_proposal(content,json.loads(text))
    else:raise ValidationError('Unsupported task')
    return content,usage_of(result,model)

def _pcm_rate(mime):
    """Sample rate from e.g. 'audio/L16;codec=pcm;rate=24000' (Gemini TTS default is 24 kHz)."""
    import re
    found=re.search(r'rate=(\d{4,6})',mime)
    rate=int(found.group(1)) if found else 24000
    if not 8000<=rate<=192000:raise ValidationError('Gemini audio sample rate unsupported')
    return rate

def generate_asset(kind,prompt,model,voice='Kore',*,projects_dir=None,aspect_ratio=None):
    allowed=(load_registry(projects_dir)['models'] if projects_dir is not None else CATALOG)
    if kind=='image':
        if model not in allowed.get('image',{}).get('gemini',[]):raise ValidationError('Unsupported Gemini image model')
        generation={'responseModalities':['IMAGE']}
        if aspect_ratio:
            # Without an explicit ratio Gemini returns square images, which cover-fit crops heavily.
            generation['imageConfig']={'aspectRatio':aspect_ratio}
        res=request_model(model,[{'text':prompt}],generation,projects_dir=projects_dir)
        for item in parts_of(res):
            if not isinstance(item, dict):
                raise ValidationError('Gemini returned malformed image part')
            data=item.get('inlineData') or {}
            if not isinstance(data, dict):
                raise ValidationError('Gemini returned malformed image data')
            if str(data.get('mimeType','')).startswith('image/'):
                try:
                    content=base64.b64decode(data['data'],validate=True)
                except (ValueError, KeyError, TypeError, base64.binascii.Error) as exc:
                    raise ValidationError('Gemini returned invalid image/audio base64') from exc
                if content.startswith(b'\x89PNG\r\n\x1a\n'):return content,'.png',model
                if content.startswith(b'\xff\xd8\xff'):return content,'.jpg',model
        raise ValidationError('Gemini returned no PNG/JPEG image')
    if kind=='audio':
        if model not in allowed.get('audio',{}).get('gemini',[]):raise ValidationError('Unsupported Gemini TTS model')
        if voice not in ('Kore','Puck','Charon','Fenrir','Aoede','Zephyr'):raise ValidationError('Unsupported Gemini voice')
        res=request_model(model,[{'text':prompt}],{'responseModalities':['AUDIO'],'speechConfig':{'voiceConfig':{'prebuiltVoiceConfig':{'voiceName':voice}}}},projects_dir=projects_dir)
        for item in parts_of(res):
            if not isinstance(item, dict):
                raise ValidationError('Gemini returned malformed audio part')
            data=item.get('inlineData') or {}
            if not isinstance(data, dict):
                raise ValidationError('Gemini returned malformed audio data')
            mime=str(data.get('mimeType','')).lower()
            if mime.startswith('audio/'):
                try:
                    content=base64.b64decode(data['data'],validate=True)
                except (ValueError, KeyError, TypeError, base64.binascii.Error) as exc:
                    raise ValidationError('Gemini returned invalid image/audio base64') from exc
                if content.startswith(b'RIFF'):return content,'.wav',model
                if 'pcm' in mime or 'l16' in mime:
                    buf=BytesIO()
                    with wave.open(buf,'wb') as out:
                        out.setnchannels(1);out.setsampwidth(2);out.setframerate(_pcm_rate(mime));out.writeframes(content)
                    return buf.getvalue(),'.wav',model
                raise ValidationError('Gemini audio encoding unsupported')
        raise ValidationError('Gemini returned no audio')
    raise ValidationError('Gemini video generation not integrated')
