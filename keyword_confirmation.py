"""Loopback-only confirmation screen; every generation requires an explicit choice."""
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from keyword_resolver import ResolutionError, clean_keyword, confirm_resolution

HTML = r'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Confirm primary keyword</title>
<style>body{font:16px system-ui;max-width:1150px;margin:40px auto;padding:20px;color:#17202a}table{border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid #ddd;text-align:left}tr.selected{background:#eaf4ff}input[type=text]{padding:10px;width:420px}button{padding:12px;margin:12px 8px 12px 0}#error{color:#a00}#comparison{display:flex;gap:25px}#comparison>p{flex:1;background:#f4f4f4;padding:15px}</style>
<h1>Confirm the primary keyword</h1><p id="title"></p><p>The title stays your H1 / angle. Choose the separate search keyword below.</p>
<p id="reason"></p><div id="comparison"></div><table><thead><tr><th>Select</th><th>Keyword</th><th>Monthly searches</th><th>Content-type match</th><th>Already ranking</th><th>Status</th></tr></thead><tbody id="rows"></tbody></table>
<p id="source"></p><div id="warnings"></div><label id="ackLabel" hidden><input type="checkbox" id="ack"> I've checked this</label>
<p><label>Your name <input type="text" id="name" autocomplete="name" required></label></p>
<p><label>Override keyword <input type="text" id="override" maxlength="100"></label> <button id="validate">Validate override</button></p>
<p id="error" role="alert"></p><button id="confirm" disabled>Confirm keyword (Enter)</button><button id="cancel">Cancel run</button>
<p>Up/Down or Left/Right changes selection. An override must be validated before confirmation. API failures block the run.</p>
<script>
const token=location.pathname.slice(1);let state=null,selected=0,busy=false;
const el=id=>document.getElementById(id);
async function call(action,data={}){const r=await fetch('/'+token+'/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const v=await r.json();if(!r.ok)throw Error(v.error);return v;}
function enabled(){el('confirm').disabled=busy||!state||!el('name').value.trim()||(state.options[selected].warnings.length&&!el('ack').checked)||el('override').value.trim()!==state.override_text;}
function render(){el('title').textContent=state.title_angle+' • '+state.content_type;el('rows').replaceChildren();state.options.forEach((r,i)=>{const tr=document.createElement('tr');tr.className=i===selected?'selected':'';const td=document.createElement('td');const radio=document.createElement('input');radio.type='radio';radio.name='candidate';radio.checked=i===selected;radio.setAttribute('aria-label',r.keyword);radio.onchange=()=>choose(i);td.append(radio);tr.append(td);[r.keyword,r.msv,r.intent_match+' ('+r.dominant_page_type+')',r.ranking_urls.join(', ')||'No',r.warnings.length?'Warning: acknowledgment required':r.status].forEach(x=>{const c=document.createElement('td');c.textContent=x;tr.append(c)});el('rows').append(tr)});
const first=state.options[0];el('reason').textContent='Recommended: '+first.keyword+' offers the best scored balance: '+first.msv+' monthly searches, '+first.relevant_pages+' relevant pages, mostly '+first.dominant_page_type+' results; '+(first.ranking_urls.length?'existing PingCAP coverage needs review.':'no PingCAP page in this top-10 snapshot.');
el('comparison').replaceChildren();if(state.close_call){state.options.slice(0,2).forEach((r,i)=>{let p=document.createElement('p');p.textContent='#'+(i+1)+' '+r.keyword+': '+r.msv+' searches; angle relevance '+Math.round(r.scores.components.relevance*100)+'%; intent fit '+Math.round(r.scores.components.intent*100)+'%.';el('comparison').append(p)});let p=document.createElement('p');const a=state.options[0],b=state.options[1];p.textContent='Close call: '+(a.msv===b.msv?'equal search volume':a.msv>b.msv?'#1 has more searches':'#2 has more searches')+'; '+(a.scores.components.relevance===b.scores.components.relevance?'equal angle relevance':a.scores.components.relevance>b.scores.components.relevance?'#1 fits the angle more closely':'#2 fits the angle more closely')+'.';el('comparison').append(p)}
const r=state.options[selected];el('warnings').textContent=r.warnings.join(' | ');el('source').textContent=r.serp_snapshot.cannibalization_source;el('ackLabel').hidden=!r.warnings.length;enabled();}
function choose(i){selected=i;el('ack').checked=false;render();}
el('ack').onchange=enabled;el('name').oninput=enabled;el('override').oninput=()=>{el('ack').checked=false;enabled()};
el('validate').onclick=async()=>{busy=true;enabled();el('error').textContent='Validating keyword…';try{state=await call('override',{keyword:el('override').value});selected=0;el('ack').checked=false;el('override').value=state.override_text;el('error').textContent='';render()}catch(e){state=null;el('error').textContent=e.message}finally{busy=false;enabled()}};
el('confirm').onclick=async()=>{if(el('confirm').disabled)return;busy=true;enabled();try{await call('confirm',{index:selected,name:el('name').value,acknowledged:el('ack').checked});document.body.textContent='Confirmed. Return to Terminal; brief generation is continuing.'}catch(e){el('error').textContent=e.message;busy=false;enabled()}};
el('cancel').onclick=async()=>{await call('cancel');document.body.textContent='Run cancelled.'};
document.addEventListener('keydown',e=>{if(e.target.tagName==='INPUT'&&e.target.type==='text'&&!(e.target.id==='name'&&e.key==='Enter'))return;if(!state||busy)return;if(['ArrowDown','ArrowRight','ArrowUp','ArrowLeft'].includes(e.key)){e.preventDefault();choose((selected+(['ArrowDown','ArrowRight'].includes(e.key)?1:-1)+state.options.length)%state.options.length)}if(e.key==='Enter'){e.preventDefault();if(!el('confirm').disabled)el('confirm').click()}});
fetch('/'+token+'/state').then(r=>r.json()).then(v=>{state=v;el('override').value=state.override_text;render()}).catch(e=>el('error').textContent=e.message);
</script></html>'''


def confirmation_screen(resolver, proposal, open_browser=webbrowser.open):
    """Serve only on loopback, with an unguessable per-run token. No auto-confirm."""
    token = secrets.token_urlsafe(32)
    done = threading.Event()
    result = {}
    current = {'proposal':proposal, 'failed':False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, data, html=False):
            body = data.encode() if html else json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            host = f'127.0.0.1:{self.server.server_port}'
            origin = self.headers.get('Origin')
            return self.headers.get('Host')==host and (origin is None or origin=='http://'+host)

        def do_GET(self):
            if not self.allowed():
                return self.send(403,{'error':'Forbidden'})
            if self.path=='/'+token:
                return self.send(200,HTML,True)
            if self.path=='/'+token+'/state':
                return self.send(200,current['proposal'])
            self.send(404,{'error':'Not found'})

        def do_POST(self):
            if not self.allowed() or not self.path.startswith('/'+token+'/'):
                return self.send(403,{'error':'Forbidden'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length < 4096:
                    raise ResolutionError('Invalid request size.')
                data = json.loads(self.rfile.read(length))
                action = self.path.rsplit('/',1)[-1]
                if action=='cancel':
                    result['error'] = ResolutionError('Keyword confirmation cancelled; run blocked.')
                    self.send(200,{'cancelled':True}); done.set(); return
                if action=='override':
                    try:
                        clean_keyword(data.get('keyword'))
                        updated = resolver.resolve(proposal['title_angle'],proposal['content_type'],data['keyword'].strip())
                    except ResolutionError as exc:
                        # An API error/hard fail cannot fall back to the old selection.
                        current['failed'] = True
                        result['error'] = exc
                        self.send(422,{'error':str(exc)}); done.set(); return
                    # Keep earlier unselected candidates, even after an override.
                    previous = {r['keyword']:r for r in current['proposal']['candidates']}
                    previous.update({r['keyword']:r for r in updated['candidates']})
                    updated['candidates'] = list(previous.values())
                    updated['scores'] = {**current['proposal']['scores'],**updated['scores']}
                    current['proposal'] = updated
                    return self.send(200,updated)
                if action=='confirm' and not current['failed']:
                    result['resolution'] = confirm_resolution(current['proposal'],data.get('index'),data.get('name'),data.get('acknowledged') is True)
                    self.send(200,{'confirmed':True}); done.set(); return
                raise ResolutionError('Invalid confirmation action.')
            except (ResolutionError,ValueError,TypeError) as exc:
                self.send(422,{'error':str(exc)})

    with HTTPServer(('127.0.0.1',0),Handler) as server:
        server.timeout = 0.5
        url = f'http://127.0.0.1:{server.server_port}/{token}'
        print('Stage 0 confirmation (required): '+url)
        print('If the browser does not open, open that URL manually. Ctrl+C cancels.')
        try:
            open_browser(url)
        except Exception:
            print('Could not open a browser automatically; use the URL above.')
        try:
            while not done.is_set():
                server.handle_request()
        except KeyboardInterrupt as exc:
            raise ResolutionError('Keyword confirmation cancelled; run blocked.') from exc
    if 'error' in result:
        raise result['error']
    return result['resolution']
