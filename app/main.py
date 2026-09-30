import json, os, secrets, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("LINA_DATA_DIR", str(BASE / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / "lina.db"
APP_NAME = os.getenv("LINA_APP_NAME", "Lina Business & Sales Manager")
OWNER = os.getenv("LINA_OWNER", "Khodor Ezzeddine")
API_KEY = os.getenv("LINA_API_KEY", "")
AUTH_DISABLED = os.getenv("LINA_AUTH_DISABLED", "false").lower() == "true"
ALLOWED_ORIGINS = [x.strip() for x in os.getenv("LINA_ALLOWED_ORIGINS", "*").split(",") if x.strip()]

app = FastAPI(title=APP_NAME, version="6.0")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET","POST","PUT","PATCH","DELETE"], allow_headers=["*"], allow_credentials=False)

@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response

def now():
    return datetime.now(timezone.utc).isoformat()

def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,country TEXT,industry TEXT,website TEXT,type TEXT,notes TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS contacts(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,name TEXT NOT NULL,title TEXT,department TEXT,email TEXT,phone TEXT,decision_level TEXT,relationship_status TEXT,notes TEXT,created_at TEXT,FOREIGN KEY(company_id) REFERENCES companies(id));
    CREATE TABLE IF NOT EXISTS opportunities(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,name TEXT NOT NULL,solution TEXT,country TEXT,project TEXT,estimated_value REAL DEFAULT 0,probability REAL DEFAULT 0,stage TEXT,priority TEXT,competitor TEXT,next_action TEXT,followup_date TEXT,notes TEXT,created_at TEXT,FOREIGN KEY(company_id) REFERENCES companies(id));
    CREATE TABLE IF NOT EXISTS activities(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,opportunity_id INTEGER,type TEXT,subject TEXT,notes TEXT,due_date TEXT,status TEXT,created_at TEXT,FOREIGN KEY(company_id) REFERENCES companies(id),FOREIGN KEY(opportunity_id) REFERENCES opportunities(id));
    CREATE TABLE IF NOT EXISTS quotations(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,opportunity_id INTEGER,quote_no TEXT,items_json TEXT,cost REAL DEFAULT 0,selling_price REAL DEFAULT 0,discount REAL DEFAULT 0,vat REAL DEFAULT 5,total REAL DEFAULT 0,payment_terms TEXT,delivery TEXT,warranty TEXT,validity_days INTEGER,approval_status TEXT DEFAULT 'DRAFT',approved_by TEXT,created_at TEXT,FOREIGN KEY(company_id) REFERENCES companies(id));
    CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,opportunity_id INTEGER,invoice_no TEXT,amount REAL DEFAULT 0,vat REAL DEFAULT 5,due_date TEXT,payment_status TEXT DEFAULT 'UNPAID',outstanding REAL DEFAULT 0,created_at TEXT,FOREIGN KEY(company_id) REFERENCES companies(id));
    CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY AUTOINCREMENT,entity_type TEXT,entity_id INTEGER,action TEXT,status TEXT DEFAULT 'PENDING',requested_by TEXT DEFAULT 'Lina',approved_by TEXT,comments TEXT,created_at TEXT,decided_at TEXT);
    CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT,action TEXT,entity_type TEXT,entity_id INTEGER,details TEXT,created_at TEXT);
    """)
    c.commit(); c.close()
init()

def audit(c, actor, action, entity_type, entity_id=None, details=""):
    c.execute("INSERT INTO audit_log(actor,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)", (actor,action,entity_type,entity_id,details,now()))

def auth(x_api_key: str | None = Header(default=None)):
    if AUTH_DISABLED:
        return "local"
    if not API_KEY:
        raise HTTPException(503, "Lina authentication is not configured")
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, "Invalid or missing X-API-Key")
    return OWNER

class Company(BaseModel):
    name:str; country:str=""; industry:str=""; website:str=""; type:str="prospect"; notes:str=""
class Contact(BaseModel):
    company_id:int; name:str; title:str=""; department:str=""; email:str=""; phone:str=""; decision_level:str=""; relationship_status:str=""; notes:str=""
class Opportunity(BaseModel):
    company_id:int; name:str; solution:str=""; country:str=""; project:str=""; estimated_value:float=0; probability:float=0; stage:str="NEW"; priority:str="DEVELOPING"; competitor:str=""; next_action:str=""; followup_date:str=""; notes:str=""
class Activity(BaseModel):
    company_id:int|None=None; opportunity_id:int|None=None; type:str="TASK"; subject:str; notes:str=""; due_date:str=""; status:str="OPEN"
class Quote(BaseModel):
    company_id:int; opportunity_id:int|None=None; quote_no:str=""; items:dict=Field(default_factory=dict); cost:float=0; selling_price:float=0; discount:float=0; vat:float=5; payment_terms:str=""; delivery:str=""; warranty:str=""; validity_days:int=30
class Invoice(BaseModel):
    company_id:int; opportunity_id:int|None=None; invoice_no:str=""; amount:float=0; vat:float=5; due_date:str=""; payment_status:str="UNPAID"; outstanding:float=0

@app.get("/health")
def health(): return {"status":"ok","service":"Lina","version":"6.0","mode":"approval-controlled"}

@app.get("/", include_in_schema=False)
def home(): return FileResponse(BASE / "static" / "index.html")

@app.get("/config")
def config(_:str=Depends(auth)): return {"app_name":APP_NAME,"owner":OWNER,"version":"6.0","approval_principle":"Lina prepares/recommends; Khodor approves; Lina executes."}

@app.get("/dashboard")
def dashboard(_:str=Depends(auth)):
    c=db();
    hot=c.execute("SELECT COUNT(*) n FROM opportunities WHERE priority='HOT'").fetchone()["n"]
    pipeline=c.execute("SELECT COALESCE(SUM(estimated_value*probability/100),0) v FROM opportunities WHERE stage NOT IN ('LOST','CANCELLED')").fetchone()["v"]
    quotes=c.execute("SELECT COUNT(*) n FROM quotations WHERE approval_status='PENDING_APPROVAL'").fetchone()["n"]
    overdue=c.execute("SELECT COUNT(*) n FROM activities WHERE status='OPEN' AND due_date<>'' AND due_date < date('now')").fetchone()["n"]
    c.close(); return {"hot_opportunities":hot,"weighted_pipeline":round(pipeline,2),"quotes_awaiting_approval":quotes,"overdue_followups":overdue}

@app.post("/companies")
def add_company(x:Company, actor:str=Depends(auth)):
    c=db(); cur=c.execute("INSERT INTO companies(name,country,industry,website,type,notes,created_at) VALUES(?,?,?,?,?,?,?)", (*x.model_dump().values(),now())); audit(c,actor,"CREATE","COMPANY",cur.lastrowid,x.name); c.commit(); out={"id":cur.lastrowid,**x.model_dump()}; c.close(); return out
@app.get("/companies")
def companies(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute("SELECT * FROM companies ORDER BY id DESC")]; c.close(); return rows
@app.post("/contacts")
def add_contact(x:Contact, actor:str=Depends(auth)):
    c=db(); cur=c.execute("INSERT INTO contacts(company_id,name,title,department,email,phone,decision_level,relationship_status,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (*x.model_dump().values(),now())); audit(c,actor,"CREATE","CONTACT",cur.lastrowid,x.name); c.commit(); out={"id":cur.lastrowid,**x.model_dump()}; c.close(); return out
@app.get("/contacts")
def contacts(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute("SELECT ct.*,co.name company_name FROM contacts ct LEFT JOIN companies co ON co.id=ct.company_id ORDER BY ct.id DESC")]; c.close(); return rows
@app.post("/opportunities")
def add_opportunity(x:Opportunity, actor:str=Depends(auth)):
    c=db(); cur=c.execute("INSERT INTO opportunities(company_id,name,solution,country,project,estimated_value,probability,stage,priority,competitor,next_action,followup_date,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*x.model_dump().values(),now())); audit(c,actor,"CREATE","OPPORTUNITY",cur.lastrowid,x.name); c.commit(); out={"id":cur.lastrowid,**x.model_dump()}; c.close(); return out
@app.get("/opportunities")
def opportunities(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute("SELECT o.*,c.name company_name FROM opportunities o LEFT JOIN companies c ON c.id=o.company_id ORDER BY o.estimated_value DESC")]; c.close(); return rows
@app.post("/activities")
def add_activity(x:Activity, actor:str=Depends(auth)):
    c=db(); cur=c.execute("INSERT INTO activities(company_id,opportunity_id,type,subject,notes,due_date,status,created_at) VALUES(?,?,?,?,?,?,?,?)", (*x.model_dump().values(),now())); audit(c,actor,"CREATE","ACTIVITY",cur.lastrowid,x.subject); c.commit(); out={"id":cur.lastrowid,**x.model_dump()}; c.close(); return out
@app.get("/activities")
def activities(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute("SELECT a.*,co.name company_name FROM activities a LEFT JOIN companies co ON co.id=a.company_id ORDER BY a.id DESC")]; c.close(); return rows
@app.post("/quotations")
def create_quote(x:Quote, actor:str=Depends(auth)):
    c=db(); subtotal=max(0,x.selling_price-x.discount); total=round(subtotal*(1+x.vat/100),2)
    cur=c.execute("INSERT INTO quotations(company_id,opportunity_id,quote_no,items_json,cost,selling_price,discount,vat,total,payment_terms,delivery,warranty,validity_days,approval_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (x.company_id,x.opportunity_id,x.quote_no,json.dumps(x.items),x.cost,x.selling_price,x.discount,x.vat,total,x.payment_terms,x.delivery,x.warranty,x.validity_days,"PENDING_APPROVAL",now()))
    qid=cur.lastrowid; c.execute("INSERT INTO approvals(entity_type,entity_id,action,status,created_at) VALUES('QUOTATION',?,'CUSTOMER_SUBMISSION','PENDING',?)",(qid,now())); audit(c,actor,"CREATE","QUOTATION",qid,x.quote_no); c.commit(); c.close()
    net=x.selling_price-x.discount; margin=round(net-x.cost,2); margin_pct=round((margin/(net or 1))*100,2)
    return {"id":qid,"subtotal":subtotal,"vat_amount":round(total-subtotal,2),"total":total,"gross_margin":margin,"margin_percent":margin_pct,"approval_status":"PENDING_APPROVAL"}
@app.get("/quotations")
def quotations(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute("SELECT q.*,co.name company_name FROM quotations q LEFT JOIN companies co ON co.id=q.company_id ORDER BY q.id DESC")]; c.close(); return rows
@app.post("/invoices")
def create_invoice(x:Invoice, actor:str=Depends(auth)):
    c=db(); cur=c.execute("INSERT INTO invoices(company_id,opportunity_id,invoice_no,amount,vat,due_date,payment_status,outstanding,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (*x.model_dump().values(),now())); audit(c,actor,"CREATE","INVOICE",cur.lastrowid,x.invoice_no); c.commit(); out={"id":cur.lastrowid,**x.model_dump()}; c.close(); return out
@app.get("/invoices")
def invoices(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute("SELECT i.*,co.name company_name FROM invoices i LEFT JOIN companies co ON co.id=i.company_id ORDER BY i.id DESC")]; c.close(); return rows
@app.get("/approvals")
def approvals(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute("SELECT * FROM approvals WHERE status='PENDING' ORDER BY id DESC")]; c.close(); return rows
@app.post("/approvals/{approval_id}/approve")
def approve(approval_id:int, actor:str=Depends(auth)):
    if actor != OWNER: raise HTTPException(403,"Only the owner can approve")
    c=db(); a=c.execute("SELECT * FROM approvals WHERE id=?",(approval_id,)).fetchone()
    if not a: c.close(); raise HTTPException(404,"approval not found")
    c.execute("UPDATE approvals SET status='APPROVED',approved_by=?,decided_at=? WHERE id=?",(OWNER,now(),approval_id))
    if a["entity_type"]=="QUOTATION": c.execute("UPDATE quotations SET approval_status='APPROVED',approved_by=? WHERE id=?",(OWNER,a["entity_id"]))
    audit(c,OWNER,"APPROVE",a["entity_type"],a["entity_id"],f"approval_id={approval_id}"); c.commit(); c.close(); return {"status":"APPROVED","approval_id":approval_id,"approved_by":OWNER}
@app.get("/reports/executive")
def report(_:str=Depends(auth)):
    c=db(); stages=[dict(r) for r in c.execute("SELECT stage,COUNT(*) count,COALESCE(SUM(estimated_value),0) value FROM opportunities GROUP BY stage")]; c.close(); return {"generated_at":now(),"stages":stages,"principle":f"Lina prepares/recommends; {OWNER} approves; Lina executes."}
@app.get("/audit")
def audit_log(_:str=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 200")]; c.close(); return rows
