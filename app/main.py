import json, os, secrets, sqlite3, urllib.parse, urllib.request, urllib.error, re, ipaddress, socket, html
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv('LINA_DATA_DIR', str(BASE / 'data')))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / 'lina.db'
APP_NAME = os.getenv('LINA_APP_NAME', 'Lina Business & Sales Manager')
OWNER = os.getenv('LINA_OWNER', 'Khodor Ezzeddine')
API_KEY = os.getenv('LINA_API_KEY', '')
BRAVE_SEARCH_API_KEY = os.getenv('BRAVE_SEARCH_API_KEY', '').strip()
AUTH_DISABLED = os.getenv('LINA_AUTH_DISABLED', 'false').lower() == 'true'
ALLOWED_ORIGINS = [x.strip() for x in os.getenv('LINA_ALLOWED_ORIGINS', '*').split(',') if x.strip()]

app = FastAPI(title=APP_NAME, version='11.2')
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=['GET','POST','PUT','PATCH','DELETE'], allow_headers=['*'], allow_credentials=False)

@app.middleware('http')
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    return response

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def init():
    c=db(); c.executescript('''
    CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,country TEXT,industry TEXT,website TEXT,type TEXT,notes TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS contacts(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,name TEXT NOT NULL,title TEXT,department TEXT,email TEXT,phone TEXT,decision_level TEXT,relationship_status TEXT,notes TEXT,created_at TEXT,FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS opportunities(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,name TEXT NOT NULL,solution TEXT,country TEXT,project TEXT,estimated_value REAL DEFAULT 0,probability REAL DEFAULT 0,stage TEXT,priority TEXT,competitor TEXT,next_action TEXT,followup_date TEXT,notes TEXT,created_at TEXT,FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS activities(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,opportunity_id INTEGER,type TEXT,subject TEXT,notes TEXT,due_date TEXT,status TEXT,created_at TEXT,FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE SET NULL,FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE SET NULL);
    CREATE TABLE IF NOT EXISTS quotations(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,opportunity_id INTEGER,quote_no TEXT,items_json TEXT,cost REAL DEFAULT 0,selling_price REAL DEFAULT 0,discount REAL DEFAULT 0,vat REAL DEFAULT 5,total REAL DEFAULT 0,payment_terms TEXT,delivery TEXT,warranty TEXT,validity_days INTEGER,approval_status TEXT DEFAULT 'DRAFT',approved_by TEXT,created_at TEXT,FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,opportunity_id INTEGER,invoice_no TEXT,amount REAL DEFAULT 0,vat REAL DEFAULT 5,due_date TEXT,payment_status TEXT DEFAULT 'UNPAID',outstanding REAL DEFAULT 0,created_at TEXT,FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY AUTOINCREMENT,entity_type TEXT,entity_id INTEGER,action TEXT,status TEXT DEFAULT 'PENDING',requested_by TEXT DEFAULT 'Lina',approved_by TEXT,comments TEXT,created_at TEXT,decided_at TEXT);
    CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT,action TEXT,entity_type TEXT,entity_id INTEGER,details TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS research_tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,country TEXT,industry TEXT,solution TEXT,objective TEXT,status TEXT DEFAULT 'OPEN',created_at TEXT);
    CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY AUTOINCREMENT,company_name TEXT NOT NULL,country TEXT DEFAULT '',industry TEXT DEFAULT '',website TEXT DEFAULT '',contact_name TEXT DEFAULT '',contact_title TEXT DEFAULT '',phone TEXT DEFAULT '',email TEXT DEFAULT '',source_url TEXT DEFAULT '',source_title TEXT DEFAULT '',source_snippet TEXT DEFAULT '',verification_status TEXT DEFAULT 'UNVERIFIED',lead_status TEXT DEFAULT 'NEW',research_query TEXT DEFAULT '',created_at TEXT); 
    CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1),company_name TEXT,country TEXT,website TEXT,sales_email TEXT,info_email TEXT,currency TEXT,phone TEXT,updated_at TEXT);
    ''');
    if not c.execute('SELECT 1 FROM settings WHERE id=1').fetchone():
        c.execute("INSERT INTO settings VALUES(1,'Matrix Business Services','Oman','https://mbs-om.com','sales@mbs-om.com','Info@mbs-om.com','OMR','',?)",(now(),))
    c.commit(); c.close()
init()

def audit(c, actor, action, entity_type, entity_id=None, details=''):
    c.execute('INSERT INTO audit_log(actor,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)',(actor,action,entity_type,entity_id,details,now()))

def auth(x_api_key: str | None = Header(default=None)):
    if AUTH_DISABLED: return 'local'
    if not API_KEY: raise HTTPException(503,'Lina authentication is not configured')
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY): raise HTTPException(401,'Invalid or missing X-API-Key')
    return OWNER

class Company(BaseModel): name:str; country:str=''; industry:str=''; website:str=''; type:str='prospect'; notes:str=''
class Contact(BaseModel): company_id:int; name:str; title:str=''; department:str=''; email:str=''; phone:str=''; decision_level:str=''; relationship_status:str=''; notes:str=''
class Opportunity(BaseModel): company_id:int; name:str; solution:str=''; country:str=''; project:str=''; estimated_value:float=0; probability:float=0; stage:str='NEW'; priority:str='DEVELOPING'; competitor:str=''; next_action:str=''; followup_date:str=''; notes:str=''
class Activity(BaseModel): company_id:int|None=None; opportunity_id:int|None=None; type:str='TASK'; subject:str; notes:str=''; due_date:str=''; status:str='OPEN'
class Quote(BaseModel): company_id:int; opportunity_id:int|None=None; quote_no:str=''; items:dict=Field(default_factory=dict); cost:float=0; selling_price:float=0; discount:float=0; vat:float=5; payment_terms:str=''; delivery:str=''; warranty:str=''; validity_days:int=30
class Invoice(BaseModel): company_id:int; opportunity_id:int|None=None; invoice_no:str=''; amount:float=0; vat:float=5; due_date:str=''; payment_status:str='UNPAID'; outstanding:float=0
class ResearchTask(BaseModel): country:str; industry:str=''; solution:str=''; objective:str=''
class Settings(BaseModel): company_name:str; country:str=''; website:str=''; sales_email:str=''; info_email:str=''; currency:str='OMR'; phone:str=''
class LeadSearch(BaseModel): country:str; industry:str=''; solution:str=''; keywords:str=''; count:int=10
class ContactDiscovery(BaseModel): url:str
class LeadCreate(BaseModel): company_name:str; country:str=''; industry:str=''; website:str=''; contact_name:str=''; contact_title:str=''; phone:str=''; email:str=''; source_url:str=''; source_title:str=''; source_snippet:str=''; verification_status:str='UNVERIFIED'; research_query:str=''

class CompanyUpdate(BaseModel): name:str; country:str=''; industry:str=''; website:str=''; type:str='prospect'; notes:str=''
class OpportunityUpdate(BaseModel): name:str; solution:str=''; country:str=''; project:str=''; estimated_value:float=0; probability:float=0; stage:str='NEW'; priority:str='DEVELOPING'; competitor:str=''; next_action:str=''; followup_date:str=''; notes:str=''
class AskRequest(BaseModel): message:str
class PaymentUpdate(BaseModel): payment_status:str; paid_amount:float|None=None

def opportunity_score(row):
    score=0
    priority={'HOT':35,'WARM':25,'DEVELOPING':15,'LOW':5}.get((row.get('priority') or '').upper(),0)
    score+=priority
    score+=min(25,max(0,float(row.get('probability') or 0))*0.25)
    score+=min(15,max(0,float(row.get('estimated_value') or 0))/10000)
    completeness=sum(bool(row.get(k)) for k in ('solution','project','next_action','followup_date'))
    score+=completeness*5
    if row.get('stage') in ('WON','LOST','CANCELLED'): score=0
    return min(100,round(score))


@app.get('/health')
def health(): return {'status':'ok','service':'Lina','version':'11.2','mode':'approval-controlled','lead_research_configured':bool(BRAVE_SEARCH_API_KEY)}
@app.get('/', include_in_schema=False)
def home(): return FileResponse(BASE/'static'/'index.html')
@app.get('/config')
def config(_:str=Depends(auth)): return {'app_name':APP_NAME,'owner':OWNER,'version':'11.2','lead_research_configured':bool(BRAVE_SEARCH_API_KEY),'approval_principle':f'Lina prepares/recommends; {OWNER} approves; Lina executes.'}

@app.get('/dashboard')
def dashboard(_:str=Depends(auth)):
    c=db();
    out={
      'hot_opportunities':c.execute("SELECT COUNT(*) n FROM opportunities WHERE priority='HOT'").fetchone()['n'],
      'weighted_pipeline':round(c.execute("SELECT COALESCE(SUM(estimated_value*probability/100),0) v FROM opportunities WHERE stage NOT IN ('LOST','CANCELLED')").fetchone()['v'],2),
      'quotes_awaiting_approval':c.execute("SELECT COUNT(*) n FROM quotations WHERE approval_status='PENDING_APPROVAL'").fetchone()['n'],
      'overdue_followups':c.execute("SELECT COUNT(*) n FROM activities WHERE status='OPEN' AND due_date<>'' AND date(due_date)<date('now')").fetchone()['n'],
      'companies':c.execute('SELECT COUNT(*) n FROM companies').fetchone()['n'],
      'contacts':c.execute('SELECT COUNT(*) n FROM contacts').fetchone()['n'],
      'opportunities':c.execute("SELECT COUNT(*) n FROM opportunities WHERE stage NOT IN ('LOST','CANCELLED')").fetchone()['n'],
      'won_value':round(c.execute("SELECT COALESCE(SUM(estimated_value),0) v FROM opportunities WHERE stage='WON'").fetchone()['v'],2)
    }; c.close(); return out

# Generic CRUD endpoints
@app.post('/companies')
def add_company(x:Company, actor:str=Depends(auth)):
    c=db(); cur=c.execute('INSERT INTO companies(name,country,industry,website,type,notes,created_at) VALUES(?,?,?,?,?,?,?)',(*x.model_dump().values(),now())); audit(c,actor,'CREATE','COMPANY',cur.lastrowid,x.name); c.commit(); out={'id':cur.lastrowid,**x.model_dump()}; c.close(); return out
@app.get('/companies')
def companies(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute('SELECT * FROM companies ORDER BY id DESC')]; c.close(); return rows
@app.put('/companies/{item_id}')
def update_company(item_id:int, x:CompanyUpdate, actor:str=Depends(auth)):
    c=db(); old=c.execute('SELECT name FROM companies WHERE id=?',(item_id,)).fetchone()
    if not old: c.close(); raise HTTPException(404,'company not found')
    c.execute('UPDATE companies SET name=?,country=?,industry=?,website=?,type=?,notes=? WHERE id=?',(*x.model_dump().values(),item_id))
    audit(c,actor,'UPDATE','COMPANY',item_id,x.name); c.commit(); out=dict(c.execute('SELECT * FROM companies WHERE id=?',(item_id,)).fetchone()); c.close(); return out

@app.delete('/companies/{item_id}')
def delete_company(item_id:int, actor:str=Depends(auth)):
    c=db(); row=c.execute('SELECT name FROM companies WHERE id=?',(item_id,)).fetchone()
    if not row: c.close(); raise HTTPException(404,'company not found')
    c.execute('DELETE FROM companies WHERE id=?',(item_id,)); audit(c,actor,'DELETE','COMPANY',item_id,row['name']); c.commit(); c.close(); return {'status':'deleted'}

@app.post('/contacts')
def add_contact(x:Contact, actor:str=Depends(auth)):
    c=db();
    if not c.execute('SELECT 1 FROM companies WHERE id=?',(x.company_id,)).fetchone(): c.close(); raise HTTPException(400,'company not found')
    cur=c.execute('INSERT INTO contacts(company_id,name,title,department,email,phone,decision_level,relationship_status,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(*x.model_dump().values(),now())); audit(c,actor,'CREATE','CONTACT',cur.lastrowid,x.name); c.commit(); out={'id':cur.lastrowid,**x.model_dump()}; c.close(); return out
@app.get('/contacts')
def contacts(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute('SELECT ct.*,co.name company_name FROM contacts ct LEFT JOIN companies co ON co.id=ct.company_id ORDER BY ct.id DESC')]; c.close(); return rows

@app.post('/opportunities')
def add_opportunity(x:Opportunity, actor:str=Depends(auth)):
    c=db();
    if not c.execute('SELECT 1 FROM companies WHERE id=?',(x.company_id,)).fetchone(): c.close(); raise HTTPException(400,'company not found')
    cur=c.execute('INSERT INTO opportunities(company_id,name,solution,country,project,estimated_value,probability,stage,priority,competitor,next_action,followup_date,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(*x.model_dump().values(),now())); audit(c,actor,'CREATE','OPPORTUNITY',cur.lastrowid,x.name); c.commit(); out={'id':cur.lastrowid,**x.model_dump()}; c.close(); return out
@app.get('/opportunities')
def opportunities(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute('SELECT o.*,c.name company_name FROM opportunities o LEFT JOIN companies c ON c.id=o.company_id ORDER BY CASE o.priority WHEN \'HOT\' THEN 1 WHEN \'WARM\' THEN 2 ELSE 3 END,o.estimated_value DESC')]; c.close(); return rows
@app.put('/opportunities/{item_id}')
def update_opportunity(item_id:int, x:OpportunityUpdate, actor:str=Depends(auth)):
    c=db(); old=c.execute('SELECT name FROM opportunities WHERE id=?',(item_id,)).fetchone()
    if not old: c.close(); raise HTTPException(404,'opportunity not found')
    if not c.execute('SELECT 1 FROM companies WHERE id=(SELECT company_id FROM opportunities WHERE id=?)',(item_id,)).fetchone(): c.close(); raise HTTPException(400,'company not found')
    c.execute('UPDATE opportunities SET name=?,solution=?,country=?,project=?,estimated_value=?,probability=?,stage=?,priority=?,competitor=?,next_action=?,followup_date=?,notes=? WHERE id=?',(*x.model_dump().values(),item_id))
    audit(c,actor,'UPDATE','OPPORTUNITY',item_id,x.name); c.commit(); out=dict(c.execute('SELECT o.*,c.name company_name FROM opportunities o LEFT JOIN companies c ON c.id=o.company_id WHERE o.id=?',(item_id,)).fetchone()); c.close(); return out


@app.post('/activities')
def add_activity(x:Activity, actor:str=Depends(auth)):
    c=db(); cur=c.execute('INSERT INTO activities(company_id,opportunity_id,type,subject,notes,due_date,status,created_at) VALUES(?,?,?,?,?,?,?,?)',(*x.model_dump().values(),now())); audit(c,actor,'CREATE','ACTIVITY',cur.lastrowid,x.subject); c.commit(); out={'id':cur.lastrowid,**x.model_dump()}; c.close(); return out
@app.get('/activities')
def activities(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute('SELECT a.*,co.name company_name FROM activities a LEFT JOIN companies co ON co.id=a.company_id ORDER BY CASE WHEN a.status=\'OPEN\' THEN 0 ELSE 1 END,a.due_date,a.id DESC')]; c.close(); return rows
@app.patch('/activities/{item_id}/complete')
def complete_activity(item_id:int, actor:str=Depends(auth)):
    c=db(); cur=c.execute("UPDATE activities SET status='DONE' WHERE id=?",(item_id,));
    if not cur.rowcount: c.close(); raise HTTPException(404,'activity not found')
    audit(c,actor,'COMPLETE','ACTIVITY',item_id); c.commit(); c.close(); return {'status':'DONE'}

@app.post('/quotations')
def create_quote(x:Quote, actor:str=Depends(auth)):
    c=db();
    if not c.execute('SELECT 1 FROM companies WHERE id=?',(x.company_id,)).fetchone(): c.close(); raise HTTPException(400,'company not found')
    subtotal=max(0,x.selling_price-x.discount); total=round(subtotal*(1+x.vat/100),2)
    quote_no=x.quote_no.strip() or f'MBS-{datetime.now().strftime("%Y%m%d")}-{c.execute("SELECT COUNT(*) n FROM quotations").fetchone()["n"]+1:04d}'
    cur=c.execute('INSERT INTO quotations(company_id,opportunity_id,quote_no,items_json,cost,selling_price,discount,vat,total,payment_terms,delivery,warranty,validity_days,approval_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(x.company_id,x.opportunity_id,quote_no,json.dumps(x.items),x.cost,x.selling_price,x.discount,x.vat,total,x.payment_terms,x.delivery,x.warranty,x.validity_days,'PENDING_APPROVAL',now()))
    qid=cur.lastrowid; c.execute("INSERT INTO approvals(entity_type,entity_id,action,status,created_at) VALUES('QUOTATION',?,'CUSTOMER_SUBMISSION','PENDING',?)",(qid,now())); audit(c,actor,'CREATE','QUOTATION',qid,quote_no); c.commit(); c.close()
    net=x.selling_price-x.discount; margin=round(net-x.cost,2); margin_pct=round((margin/(net or 1))*100,2)
    return {'id':qid,'quote_no':quote_no,'subtotal':subtotal,'vat_amount':round(total-subtotal,2),'total':total,'gross_margin':margin,'margin_percent':margin_pct,'approval_status':'PENDING_APPROVAL'}
@app.get('/quotations')
def quotations(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute('SELECT q.*,co.name company_name FROM quotations q LEFT JOIN companies co ON co.id=q.company_id ORDER BY q.id DESC')]; c.close(); return rows

@app.post('/invoices')
def create_invoice(x:Invoice, actor:str=Depends(auth)):
    c=db(); cur=c.execute('INSERT INTO invoices(company_id,opportunity_id,invoice_no,amount,vat,due_date,payment_status,outstanding,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(*x.model_dump().values(),now())); audit(c,actor,'CREATE','INVOICE',cur.lastrowid,x.invoice_no); c.commit(); out={'id':cur.lastrowid,**x.model_dump()}; c.close(); return out
@app.get('/invoices')
def invoices(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute('SELECT i.*,co.name company_name FROM invoices i LEFT JOIN companies co ON co.id=i.company_id ORDER BY i.id DESC')]; c.close(); return rows
@app.post('/invoices/from-quote/{quote_id}')
def invoice_from_quote(quote_id:int, actor:str=Depends(auth)):
    c=db(); q=c.execute('SELECT * FROM quotations WHERE id=?',(quote_id,)).fetchone()
    if not q: c.close(); raise HTTPException(404,'quotation not found')
    if q['approval_status']!='APPROVED': c.close(); raise HTTPException(400,'quotation must be approved before invoicing')
    exists=c.execute('SELECT id FROM invoices WHERE invoice_no=?',(f'INV-{q["quote_no"]}',)).fetchone()
    if exists: c.close(); raise HTTPException(409,'invoice already created from this quotation')
    inv_no=f'INV-{q["quote_no"]}'
    due=''
    cur=c.execute('INSERT INTO invoices(company_id,opportunity_id,invoice_no,amount,vat,due_date,payment_status,outstanding,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(q['company_id'],q['opportunity_id'],inv_no,q['total'],q['vat'],due,'UNPAID',q['total'],now()))
    audit(c,actor,'CREATE_FROM_QUOTE','INVOICE',cur.lastrowid,inv_no); c.commit(); out=dict(c.execute('SELECT i.*,co.name company_name FROM invoices i LEFT JOIN companies co ON co.id=i.company_id WHERE i.id=?',(cur.lastrowid,)).fetchone()); c.close(); return out


@app.get('/approvals')
def approvals(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute("SELECT a.*,q.quote_no,co.name company_name,q.total FROM approvals a LEFT JOIN quotations q ON a.entity_type='QUOTATION' AND q.id=a.entity_id LEFT JOIN companies co ON q.company_id=co.id WHERE a.status='PENDING' ORDER BY a.id DESC")]; c.close(); return rows
@app.post('/approvals/{approval_id}/approve')
def approve(approval_id:int, actor:str=Depends(auth)):
    if actor != OWNER and not AUTH_DISABLED: raise HTTPException(403,'Only the owner can approve')
    c=db(); a=c.execute('SELECT * FROM approvals WHERE id=? AND status=\'PENDING\'',(approval_id,)).fetchone()
    if not a: c.close(); raise HTTPException(404,'pending approval not found')
    c.execute('UPDATE approvals SET status=\'APPROVED\',approved_by=?,decided_at=? WHERE id=?',(OWNER,now(),approval_id))
    if a['entity_type']=='QUOTATION': c.execute('UPDATE quotations SET approval_status=\'APPROVED\',approved_by=? WHERE id=?',(OWNER,a['entity_id']))
    audit(c,OWNER,'APPROVE',a['entity_type'],a['entity_id'],f'approval_id={approval_id}'); c.commit(); c.close(); return {'status':'APPROVED','approval_id':approval_id,'approved_by':OWNER}
@app.post('/approvals/{approval_id}/reject')
def reject(approval_id:int, actor:str=Depends(auth)):
    if actor != OWNER and not AUTH_DISABLED: raise HTTPException(403,'Only the owner can reject')
    c=db(); a=c.execute('SELECT * FROM approvals WHERE id=? AND status=\'PENDING\'',(approval_id,)).fetchone()
    if not a: c.close(); raise HTTPException(404,'pending approval not found')
    c.execute("UPDATE approvals SET status='REJECTED',approved_by=?,decided_at=? WHERE id=?",(OWNER,now(),approval_id))
    if a['entity_type']=='QUOTATION': c.execute("UPDATE quotations SET approval_status='REJECTED',approved_by=? WHERE id=?",(OWNER,a['entity_id']))
    audit(c,OWNER,'REJECT',a['entity_type'],a['entity_id'],f'approval_id={approval_id}'); c.commit(); c.close(); return {'status':'REJECTED','approval_id':approval_id}

@app.post('/research-tasks')
def create_research(x:ResearchTask, actor:str=Depends(auth)):
    c=db(); cur=c.execute('INSERT INTO research_tasks(country,industry,solution,objective,status,created_at) VALUES(?,?,?,?,?,?)',(x.country,x.industry,x.solution,x.objective,'OPEN',now())); audit(c,actor,'CREATE','RESEARCH_TASK',cur.lastrowid,f'{x.country} / {x.industry} / {x.solution}'); c.commit(); out={'id':cur.lastrowid,**x.model_dump(),'status':'OPEN'}; c.close(); return out
@app.get('/research-tasks')
def research(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute('SELECT * FROM research_tasks ORDER BY id DESC')]; c.close(); return rows

# Buyer-first prospecting is a research aid, not proof of purchase intent.
BUYERS = {
    'education': 'private school university college campus educational institution',
    'healthcare': 'hospital clinic medical centre healthcare provider',
    'government': 'ministry municipality government authority public institution',
    'banking': 'bank financial institution credit union',
    'hospitality': 'hotel resort hospitality group',
    'corporate': 'corporate headquarters business enterprise',
    'manufacturing': 'factory manufacturing plant industrial company',
    'retail': 'shopping mall supermarket retail chain',
}
SUPPLIER_MARKERS = ('supplier', 'suppliers', 'distributor', 'dealer', 'reseller', 'wholesaler',
    'manufacturer', 'solutions provider', 'systems integrator', 'buy now', 'shop now', 'for sale',
    'we supply', 'our products', 'we install', 'price list', 'top 5', 'top 10', 'best suppliers')
BUYER_MARKERS = ('school', 'university', 'college', 'academy', 'campus', 'hospital', 'clinic',
    'ministry', 'municipality', 'government', 'bank', 'hotel', 'resort', 'factory',
    'retail chain', 'shopping mall', 'institution')
SIGNAL_MARKERS = ('tender', 'rfp', 'request for proposal', 'procurement notice', 'invitation to bid',
    'request for quotation', 'expansion project', 'new campus')

def classify_result(item):
    title=item.get('title','') or ''
    desc=item.get('description','') or ''
    blob=(title+' '+desc).lower()
    supplier=any(t in blob for t in SUPPLIER_MARKERS)
    buyer=any(t in blob for t in BUYER_MARKERS)
    signals=[t for t in SIGNAL_MARKERS if t in blob]
    if supplier: category='SUPPLIER_OR_COMPETITOR'
    elif buyer: category='POTENTIAL_BUYER'
    else: category='UNCLASSIFIED'
    score= (55 if category=='POTENTIAL_BUYER' else 10 if supplier else 25) + (15 if signals else 0)
    return {'category':category,'lead_score':min(score,70),'buying_signals':signals,
            'qualification_note':'Keyword-based classification; manually verify the organization and any buying signal.'}

def brave_search(query,count):
    url='https://api.search.brave.com/res/v1/web/search?'+urllib.parse.urlencode({'q':query,'count':count})
    req=urllib.request.Request(url,headers={'Accept':'application/json','X-Subscription-Token':BRAVE_SEARCH_API_KEY,'User-Agent':'LinaBusinessAgent/11.0'})
    try:
        with urllib.request.urlopen(req,timeout=20) as response: payload=json.loads(response.read(750000).decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise HTTPException(502, 'Search provider rejected the request; check API access and rate limits.' if e.code in (401,403,429) else f'Search provider HTTP error {e.code}.')
    except (TimeoutError,urllib.error.URLError,ValueError):
        raise HTTPException(502,'Web search failed to respond. Please retry later.')
    return payload.get('web',{}).get('results',[])

@app.post('/lead-hunter/search')
def lead_hunter_search(x:LeadSearch, actor:str=Depends(auth)):
    if not BRAVE_SEARCH_API_KEY: raise HTTPException(503,'Configure BRAVE_SEARCH_API_KEY in Render Environment.')
    if not x.country.strip(): raise HTTPException(400,'Target country is required')
    count=max(1,min(int(x.count or 10),20))
    buyers=BUYERS.get(x.industry.lower().strip(), 'organization institution')
    # Do not require the product keyword: it tends to retrieve vendors instead of buyers.
    query=f'{buyers} {x.country} official website contact procurement'
    if x.keywords.strip():
        query+=' '+ ' '.join(x.keywords.split()[:8])
    raw=brave_search(query,count)
    results=[]; seen=set()
    for item in raw:
        link=item.get('url','')
        parsed=urllib.parse.urlsplit(link)
        if parsed.scheme!='https' or not parsed.hostname or link in seen: continue
        seen.add(link)
        row={'title':item.get('title',''),'url':link,'description':item.get('description',''),
             'source':'Brave Search','verification_status':'UNVERIFIED'}
        row.update(classify_result(row)); results.append(row)
    results.sort(key=lambda r:(r['category']!='POTENTIAL_BUYER',-r['lead_score']))
    c=db(); cur=c.execute('INSERT INTO research_tasks(country,industry,solution,objective,status,created_at) VALUES(?,?,?,?,?,?)',
        (x.country,x.industry,x.solution,'Buyer-first search: '+query,'COMPLETED' if results else 'NO_RESULTS',now()))
    task_id=cur.lastrowid; audit(c,actor,'WEB_SEARCH','RESEARCH_TASK',task_id,query); c.commit(); c.close()
    return {'query':query,'count':len(results),'results':results,'task_id':task_id,
        'notice':'Buyer categories and scores are preliminary. Results are not confirmed purchasing needs. Use Discover public contacts on relevant official websites.'}

# Only fetch public HTTPS pages. Reject internal/reserved IP addresses and redirects.
def public_host(host):
    if not host or host.lower() in ('localhost',) or host.lower().endswith(('.local','.internal')): return False
    try:
        addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(entry[4][0]).is_global for entry in addresses)
    except (OSError,ValueError): return False

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None

CONTACT_LINK_RE=re.compile(r'href\s*=\s*[\"\']([^\"\']+)[\"\']',re.I)
EMAIL_RE=re.compile(r'(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])',re.I)
PHONE_RE=re.compile(r'(?<!\w)(?:\+?\d[\d\s().-]{7,18}\d)(?!\w)')

def fetch_public_html(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443) or not public_host(parsed.hostname):
        raise HTTPException(400,'Only public HTTPS websites are supported.')
    opener=urllib.request.build_opener(NoRedirect)
    req=urllib.request.Request(url,headers={'User-Agent':'LinaBusinessAgent/11.0 (+public business contact research)','Accept':'text/html'})
    try:
        with opener.open(req,timeout=7) as resp:
            if 'text/html' not in resp.headers.get('Content-Type','').lower(): raise HTTPException(422,'Source is not an HTML page.')
            return resp.read(200000).decode('utf-8','replace')
    except urllib.error.HTTPError as exc:
        raise HTTPException(422,'Website could not be read without redirect or restriction.')
    except (urllib.error.URLError,TimeoutError,OSError):
        raise HTTPException(502,'Website could not be reached.')

@app.post('/lead-hunter/discover-contacts')
def discover_contacts(x:ContactDiscovery, actor:str=Depends(auth)):
    url=x.url.strip()
    parsed=urllib.parse.urlsplit(url)
    if len(url)>2048 or parsed.scheme!='https': raise HTTPException(400,'A public HTTPS source URL is required.')
    origin=f'https://{parsed.hostname}' if parsed.hostname else ''
    if not origin: raise HTTPException(400,'Invalid source URL')
    html_pages=[]; first=fetch_public_html(url); html_pages.append((url,first))
    links=[]
    for href in CONTACT_LINK_RE.findall(first):
        candidate=urllib.parse.urljoin(url,html.unescape(href))
        cp=urllib.parse.urlsplit(candidate)
        if cp.hostname==parsed.hostname and cp.scheme=='https' and any(word in cp.path.lower() for word in ('contact','about','directory')):
            if candidate not in links and candidate!=url: links.append(candidate)
    for link in links[:2]:
        try: html_pages.append((link,fetch_public_html(link)))
        except HTTPException: pass
    emails={}; phones={}
    for page_url,markup in html_pages:
        cleaned=re.sub(r'<(script|style|svg|noscript)\b[^>]*>.*?</\1>',' ',markup,flags=re.I|re.S)
        # Keep line boundaries: flattening an entire page creates false phone matches
        # from unrelated years, postal codes, or neighboring numeric fields.
        text=re.sub(r'</(?:p|div|li|tr|h[1-6]|address|br)\s*>','\n',cleaned,flags=re.I)
        text=html.unescape(re.sub(r'<[^>]+>',' ',text))
        for address in EMAIL_RE.findall(text+' '+' '.join(re.findall(r'mailto:([^"\'<>? ]+)',markup,re.I))):
            if len(address)<120 and not address.lower().endswith(('.png','.jpg','.webp','.svg')):
                emails.setdefault(address.lower(),page_url)
        # Prioritize explicitly published click-to-call numbers.
        candidates=[html.unescape(v) for v in re.findall(r'href\s*=\s*["\']tel:([^"\'<>]+)',markup,re.I)]
        # Also consider short contact-labelled lines, never arbitrary page-wide numbers.
        for line in text.splitlines():
            line=re.sub(r'\s+',' ',line).strip()
            if len(line)>180 or not re.search(r'\b(?:tel|telephone|phone|mobile|call|contact|switchboard|fax)\b|هاتف|اتصل|تليفون',line,re.I):
                continue
            candidates.extend(PHONE_RE.findall(line))
        for candidate in candidates:
            candidate=urllib.parse.unquote(candidate).split(';')[0].strip()
            for match in PHONE_RE.findall(candidate):
                digits=re.sub(r'\D','',match)
                if not 8<=len(digits)<=15: continue
                # Reject date-like or obvious sequential strings; retain source for review.
                if re.fullmatch(r'(?:19|20)\d{6,}',digits) or len(set(digits))==1: continue
                normalized=('+' if match.lstrip().startswith('+') else '')+digits
                phones.setdefault(normalized,page_url)
    c=db(); audit(c,actor,'CONTACT_DISCOVERY','WEB_SOURCE',None,url); c.commit(); c.close()
    return {'source_url':url,'pages_checked':[p for p,_ in html_pages],
        'emails':[{'value':v,'source_url':u,'verification_status':'FOUND_ON_PAGE_NOT_VERIFIED'} for v,u in list(emails.items())[:8]],
        'phones':[{'value':v,'source_url':u,'verification_status':'FOUND_ON_PAGE_NOT_VERIFIED'} for v,u in list(phones.items())[:8]],
        'notice':'Automatically extracted public page text. Check each number/email manually; page presence does not establish ownership, current validity or a purchasing requirement.'}

@app.get('/leads')
def list_leads(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute('SELECT * FROM leads ORDER BY id DESC')]; c.close(); return rows

@app.post('/leads')
def create_lead(x:LeadCreate, actor:str=Depends(auth)):
    if not x.company_name.strip(): raise HTTPException(400,'Company name is required')
    status=x.verification_status.upper()
    if status not in {'UNVERIFIED','PARTIALLY_VERIFIED','VERIFIED'}: raise HTTPException(400,'Invalid verification status')
    c=db(); cur=c.execute("INSERT INTO leads(company_name,country,industry,website,contact_name,contact_title,phone,email,source_url,source_title,source_snippet,verification_status,lead_status,research_query,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(x.company_name.strip(),x.country,x.industry,x.website,x.contact_name,x.contact_title,x.phone,x.email,x.source_url,x.source_title,x.source_snippet,status,'NEW',x.research_query,now())); audit(c,actor,'CREATE','LEAD',cur.lastrowid,x.company_name); c.commit(); out=dict(c.execute('SELECT * FROM leads WHERE id=?',(cur.lastrowid,)).fetchone()); c.close(); return out

@app.put('/leads/{lead_id}')
def update_lead(lead_id:int, x:LeadCreate, actor:str=Depends(auth)):
    status=x.verification_status.upper()
    if status not in {'UNVERIFIED','PARTIALLY_VERIFIED','VERIFIED'}: raise HTTPException(400,'Invalid verification status')
    c=db(); old=c.execute('SELECT id FROM leads WHERE id=?',(lead_id,)).fetchone()
    if not old: c.close(); raise HTTPException(404,'lead not found')
    c.execute('UPDATE leads SET company_name=?,country=?,industry=?,website=?,contact_name=?,contact_title=?,phone=?,email=?,source_url=?,source_title=?,source_snippet=?,verification_status=?,research_query=? WHERE id=?',(x.company_name.strip(),x.country,x.industry,x.website,x.contact_name,x.contact_title,x.phone,x.email,x.source_url,x.source_title,x.source_snippet,status,x.research_query,lead_id))
    audit(c,actor,'UPDATE','LEAD',lead_id,x.company_name); c.commit(); out=dict(c.execute('SELECT * FROM leads WHERE id=?',(lead_id,)).fetchone()); c.close(); return out

@app.post('/leads/{lead_id}/promote')
def promote_lead(lead_id:int, actor:str=Depends(auth)):
    c=db(); lead=c.execute('SELECT * FROM leads WHERE id=?',(lead_id,)).fetchone()
    if not lead: c.close(); raise HTTPException(404,'lead not found')
    existing=c.execute('SELECT id FROM companies WHERE lower(name)=lower(?) AND lower(COALESCE(country,\'\'))=lower(?)',(lead['company_name'],lead['country'])).fetchone()
    if existing:
        company_id=existing['id']
    else:
        cur=c.execute('INSERT INTO companies(name,country,industry,website,type,notes,created_at) VALUES(?,?,?,?,?,?,?)',(lead['company_name'],lead['country'],lead['industry'],lead['website'],'prospect',f"Imported from lead #{lead_id}. Source: {lead['source_url']}",now())); company_id=cur.lastrowid
    if lead['contact_name'] and not c.execute('SELECT 1 FROM contacts WHERE company_id=? AND lower(name)=lower(?)',(company_id,lead['contact_name'])).fetchone():
        c.execute('INSERT INTO contacts(company_id,name,title,email,phone,notes,created_at) VALUES(?,?,?,?,?,?,?)',(company_id,lead['contact_name'],lead['contact_title'],lead['email'],lead['phone'],f"Source: {lead['source_url']} | Verification: {lead['verification_status']}",now()))
    c.execute("UPDATE leads SET lead_status='PROMOTED' WHERE id=?",(lead_id,)); audit(c,actor,'PROMOTE_TO_CRM','LEAD',lead_id,f'company_id={company_id}'); c.commit(); c.close(); return {'status':'PROMOTED','company_id':company_id,'verification_status':lead['verification_status'],'note':'Lead imported to CRM. Verify source and contact details before outreach.'}

@app.get('/settings')
def get_settings(_:str=Depends(auth)):
    c=db(); row=dict(c.execute('SELECT * FROM settings WHERE id=1').fetchone()); c.close(); return row
@app.put('/settings')
def put_settings(x:Settings, actor:str=Depends(auth)):
    c=db(); c.execute('UPDATE settings SET company_name=?,country=?,website=?,sales_email=?,info_email=?,currency=?,phone=?,updated_at=? WHERE id=1',(*x.model_dump().values(),now())); audit(c,actor,'UPDATE','SETTINGS',1,x.company_name); c.commit(); out=dict(c.execute('SELECT * FROM settings WHERE id=1').fetchone()); c.close(); return out

@app.get('/intelligence/opportunities')
def intelligence_opportunities(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute("SELECT o.*,c.name company_name FROM opportunities o LEFT JOIN companies c ON c.id=o.company_id WHERE o.stage NOT IN ('LOST','CANCELLED') ORDER BY o.id DESC")]; c.close()
    for r in rows:
        r['score']=opportunity_score(r)
        if r['score']>=80: r['recommendation']='Immediate action: contact decision maker / advance proposal.'
        elif r['score']>=60: r['recommendation']='Follow up and strengthen qualification.'
        elif r['score']>=40: r['recommendation']='Develop account information and next action.'
        else: r['recommendation']='Keep under development; verify need and timing.'
    return rows

@app.get('/intelligence/next-actions')
def intelligence_next_actions(_:str=Depends(auth)):
    c=db()
    rows=[dict(r) for r in c.execute("SELECT a.id,a.subject,a.due_date,a.status,a.type,c.name company_name FROM activities a LEFT JOIN companies c ON c.id=a.company_id WHERE a.status='OPEN' ORDER BY CASE WHEN a.due_date='' THEN 1 ELSE 0 END,a.due_date,a.id LIMIT 20")]
    overdue=[r for r in rows if r.get('due_date') and r['due_date'] < datetime.now(timezone.utc).date().isoformat()]
    c.close(); return {'overdue':overdue,'upcoming':[r for r in rows if r not in overdue], 'principle':f'Lina recommends; {OWNER} approves commercial commitments.'}

@app.patch('/invoices/{item_id}/payment')
def update_payment(item_id:int, x:PaymentUpdate, actor:str=Depends(auth)):
    allowed={'UNPAID','PARTIAL','PAID','OVERDUE'}
    if x.payment_status not in allowed: raise HTTPException(400,'invalid payment status')
    c=db(); row=c.execute('SELECT * FROM invoices WHERE id=?',(item_id,)).fetchone()
    if not row: c.close(); raise HTTPException(404,'invoice not found')
    if x.payment_status=='PAID': outstanding=0
    elif x.paid_amount is not None: outstanding=max(0,round(row['amount']-x.paid_amount,2))
    else: outstanding=row['outstanding']
    status='PAID' if outstanding==0 else x.payment_status
    c.execute('UPDATE invoices SET payment_status=?,outstanding=? WHERE id=?',(status,outstanding,item_id)); audit(c,actor,'UPDATE_PAYMENT','INVOICE',item_id,f'{status}; outstanding={outstanding}'); c.commit(); out=dict(c.execute('SELECT i.*,co.name company_name FROM invoices i LEFT JOIN companies co ON co.id=i.company_id WHERE i.id=?',(item_id,)).fetchone()); c.close(); return out

@app.get('/reports/executive')
def report(_:str=Depends(auth)):
    c=db();
    stages=[dict(r) for r in c.execute('SELECT stage,COUNT(*) count,COALESCE(SUM(estimated_value),0) value FROM opportunities GROUP BY stage ORDER BY stage')]
    priority=[dict(r) for r in c.execute('SELECT priority,COUNT(*) count,COALESCE(SUM(estimated_value),0) value FROM opportunities GROUP BY priority')]
    c.close(); return {'generated_at':now(),'stages':stages,'priority':priority,'principle':f'Lina prepares/recommends; {OWNER} approves; Lina executes.'}
@app.get('/audit')
def audit_log(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute('SELECT * FROM audit_log ORDER BY id DESC LIMIT 200')]; c.close(); return rows

@app.post('/ask')
def ask(payload:AskRequest, actor:str=Depends(auth)):
    q=payload.message.strip()
    if not q: return {'answer':'Tell me what you need.','data':{}}
    low=q.lower()
    c=db()
    if any(k in low for k in ('hot','warm','opportunit','pipeline','deal')):
        rows=[dict(r) for r in c.execute("SELECT o.name,o.solution,o.estimated_value,o.probability,o.stage,o.priority,o.next_action,o.followup_date,c.name company_name FROM opportunities o LEFT JOIN companies c ON c.id=o.company_id WHERE o.stage NOT IN ('LOST','CANCELLED') ORDER BY CASE o.priority WHEN 'HOT' THEN 1 WHEN 'WARM' THEN 2 ELSE 3 END,o.estimated_value DESC LIMIT 30")]
        c.close(); return {'answer':f'I found {len(rows)} active opportunities in the live CRM. I have not changed anything.','data':rows}
    if any(k in low for k in ('follow','overdue','today','task','activity')):
        rows=[dict(r) for r in c.execute("SELECT a.id,a.subject,a.due_date,a.status,a.type,c.name company_name FROM activities a LEFT JOIN companies c ON c.id=a.company_id WHERE a.status='OPEN' ORDER BY CASE WHEN a.due_date='' THEN 1 ELSE 0 END,a.due_date,a.id DESC LIMIT 50")]
        c.close(); return {'answer':f'I found {len(rows)} open follow-up actions in the live CRM.','data':rows}
    if any(k in low for k in ('quote','quotation','approval')):
        rows=[dict(r) for r in c.execute("SELECT q.quote_no,q.total,q.approval_status,q.selling_price,q.discount,c.name company_name FROM quotations q LEFT JOIN companies c ON c.id=q.company_id ORDER BY q.id DESC LIMIT 30")]
        c.close(); return {'answer':f'I found {len(rows)} quotations. Customer submission remains approval-controlled.','data':rows}
    if any(k in low for k in ('invoice','billing','payment','receivable')):
        rows=[dict(r) for r in c.execute("SELECT i.invoice_no,i.amount,i.outstanding,i.payment_status,i.due_date,c.name company_name FROM invoices i LEFT JOIN companies c ON c.id=i.company_id ORDER BY i.id DESC LIMIT 30")]
        c.close(); return {'answer':f'I found {len(rows)} invoices in the live database.','data':rows}
    if any(k in low for k in ('score','priority','recommend','next action','what should','focus')):
        rows=[dict(r) for r in c.execute("SELECT o.*,c.name company_name FROM opportunities o LEFT JOIN companies c ON c.id=o.company_id WHERE o.stage NOT IN ('LOST','CANCELLED') ORDER BY o.id DESC LIMIT 50")]
        for r in rows: r['score']=opportunity_score(r)
        rows=sorted(rows,key=lambda x:x['score'],reverse=True)[:15]
        c.close(); return {'answer':'I ranked the active opportunities using Lina’s qualification model. This is a recommendation, not an automatic commercial decision.','data':rows}
    if any(k in low for k in ('customer','company','account','contact')):
        rows=[dict(r) for r in c.execute('SELECT name,country,industry,type,website FROM companies ORDER BY id DESC LIMIT 50')]
        c.close(); return {'answer':f'The live CRM currently contains {len(rows)} companies.','data':rows}
    d={
      'hot_opportunities':c.execute("SELECT COUNT(*) n FROM opportunities WHERE priority='HOT'").fetchone()['n'],
      'weighted_pipeline':round(c.execute("SELECT COALESCE(SUM(estimated_value*probability/100),0) v FROM opportunities WHERE stage NOT IN ('LOST','CANCELLED')").fetchone()['v'],2),
      'quotes_awaiting_approval':c.execute("SELECT COUNT(*) n FROM quotations WHERE approval_status='PENDING_APPROVAL'").fetchone()['n'],
      'overdue_followups':c.execute("SELECT COUNT(*) n FROM activities WHERE status='OPEN' AND due_date<>'' AND date(due_date)<date('now')").fetchone()['n']
    }
    c.close(); return {'answer':f'Current live position: {d["hot_opportunities"]} HOT opportunities, OMR {d["weighted_pipeline"]:,.2f} weighted pipeline, {d["quotes_awaiting_approval"]} quotations awaiting approval, and {d["overdue_followups"]} overdue follow-ups. I will not invent missing information or make commercial commitments without your approval.','data':d}
