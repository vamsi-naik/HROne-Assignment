import os, re, calendar
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from pymongo import MongoClient, ReturnDocument, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from bson import json_util

load_dotenv()
MONGO_URI=os.getenv('MONGO_URI','mongodb://localhost:27017')
MONGO_DB=os.getenv('MONGO_DB','attendance_db')
client=MongoClient(MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=3000)
db=client[MONGO_DB]
employees=db.employees
logs=db.attendance_logs
UTC=timezone.utc
IST=timezone(timedelta(hours=5, minutes=30))
PRESENCE={'PRESENT','WFH','ON_DUTY'}
STATUSES={'PRESENT','ABSENT','LEAVE','WFH','ON_DUTY'}
app=FastAPI(title='Employee Attendance & Analytics API',version='2.0.0')

@app.on_event('startup')
def indexes():
    employees.create_index([('emp_code',ASCENDING)],unique=True,name='uq_emp_code')
    employees.create_index([('department',ASCENDING),('joined_on',ASCENDING)],name='department_joined')
    logs.create_index([('emp_code',ASCENDING),('date',ASCENDING)],unique=True,name='uq_attendance_emp_date')
    logs.create_index([('date',DESCENDING),('emp_code',ASCENDING)],name='attendance_date_emp')
    logs.create_index([('emp_code',ASCENDING),('date',ASCENDING),('status',ASCENDING)],name='attendance_emp_date_status')
    logs.create_index([('status',ASCENDING),('date',ASCENDING)],name='attendance_status_date')

def now_utc(): return datetime.now(UTC).replace(microsecond=0)
def dt_from_ms(v):
    if v is None: return None
    if type(v) is not int or v < 100000000000 or v > 4102444800000: raise HTTPException(422,'timestamp must be epoch milliseconds')
    return datetime.fromtimestamp(v/1000,UTC).replace(microsecond=0)
def ms(v):
    if v is None:return None
    if v.tzinfo is None:v=v.replace(tzinfo=UTC)
    return int(v.timestamp()*1000)
def public_doc(d):
    d=dict(d); d.pop('_id',None)
    for k in ('punch_in','punch_out','created_at'):
        if k in d:d[k]=ms(d[k])
    d.setdefault('history',[]); d.setdefault('half_day',False); d.setdefault('late_minutes',0); d.setdefault('overtime_minutes',0)
    for h in d.get('history',[]):
        h['at']=ms(h.get('at'))
        for fld,ch in h.get('changes',{}).items():
            if fld in ('punch_in','punch_out'):
                ch['from']=ms(ch.get('from'));ch['to']=ms(ch.get('to'))
    return d
def time_parts(s):
    try:
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',s):raise ValueError()
        h,m=map(int,s.split(':'));return h,m
    except Exception:raise HTTPException(422,'time must be HH:MM')
def attendance_date(punch, emp):
    local=punch.astimezone(IST); sh,smn=time_parts(emp['shift_start']); eh,emn=time_parts(emp['shift_end'])
    start=sh*60+smn; end=eh*60+emn; cur=local.hour*60+local.minute
    d=local.date()
    if end<=start and cur<end:d-=timedelta(days=1)
    return d.isoformat()
def shift_start_dt(date_str, shift_start):
    h,m=time_parts(shift_start); return datetime.combine(date.fromisoformat(date_str),time(h,m),IST).astimezone(UTC)
def shift_end_dt(date_str, emp):
    h,m=time_parts(emp['shift_start']); eh,em=time_parts(emp['shift_end'])
    d=date.fromisoformat(date_str)
    if eh*60+em<=h*60+m:d+=timedelta(days=1)
    return datetime.combine(d,time(eh,em),IST).astimezone(UTC)
def calc_late(pin, date_str, emp):
    start=shift_start_dt(date_str,emp['shift_start']); seconds=int((pin-start).total_seconds())
    return seconds//60 if seconds>600 else 0
def calc_derived(pin,pout,date_str,emp):
    late=calc_late(pin,date_str,emp) if pin else 0
    if not pin or not pout:return {'late_minutes':late,'work_hours':None,'overtime_minutes':0,'half_day':False}
    secs=int((pout-pin).total_seconds())
    hours=float((Decimal(secs)/Decimal(3600)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
    overtime=max(0,int((pout-shift_end_dt(date_str,emp)).total_seconds())//60)
    if overtime<30:overtime=0
    return {'late_minutes':late,'work_hours':hours,'overtime_minutes':overtime,'half_day':hours<4.50}
def month_bounds(month):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):raise HTTPException(422,'month must be YYYY-MM')
    y,m=map(int,month.split('-')); return date(y,m,1),date(y,m,calendar.monthrange(y,m)[1])
def workdays(a,b):
    n=0
    while a<=b:
        if a.weekday()<5:n+=1
        a+=timedelta(days=1)
    return n
def round2(v): return float(Decimal(str(v)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
def round4(v): return float(Decimal(str(v)).quantize(Decimal('0.0001'),rounding=ROUND_HALF_UP))
def validate_page(page,page_size):
    if page<1 or not 1<=page_size<=100:raise HTTPException(422,'invalid pagination')

class EmployeeIn(BaseModel):
    emp_code:str=Field(pattern=r'^EMP\d{4,6}$'); name:str=Field(min_length=1,max_length=100); email:str=Field(max_length=120,pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$'); department:str=Field(min_length=1,max_length=50)
    shift_start:str='09:30'; shift_end:str='18:30'; joined_on:str
    @field_validator('shift_start','shift_end')
    @classmethod
    def valid_time(cls,v):time_parts(v);return v
    @field_validator('joined_on')
    @classmethod
    def valid_date(cls,v):
        try:
            if date.fromisoformat(v).isoformat()!=v:raise ValueError()
        except Exception:raise ValueError('joined_on must be YYYY-MM-DD')
        return v
    @model_validator(mode='after')
    def shift_diff(self):
        if self.shift_start==self.shift_end:raise ValueError('shift times must differ')
        return self
class PunchIn(BaseModel):
    emp_code:str; punched_at:Optional[int]=None; status:Literal['PRESENT','WFH','ON_DUTY']='PRESENT'
    @field_validator('punched_at')
    @classmethod
    def valid_ts(cls,v):
        if v is not None:dt_from_ms(v)
        return v
class PunchOut(BaseModel):
    emp_code:str; punched_at:Optional[int]=None
    @field_validator('punched_at')
    @classmethod
    def valid_ts(cls,v):
        if v is not None:dt_from_ms(v)
        return v
class Regularize(BaseModel):
    status:Optional[Literal['PRESENT','ABSENT','LEAVE','WFH','ON_DUTY']]=None
    punch_in:Optional[int]=None; punch_out:Optional[int]=None
    reason:str=Field(min_length=5,max_length=200); regularized_by:str=Field(min_length=1,max_length=50)
    @field_validator('punch_in','punch_out')
    @classmethod
    def valid_ts(cls,v):
        if v is not None:dt_from_ms(v)
        return v

@app.get('/health')
def health():
    try: client.admin.command('ping');return {'status':'ok'}
    except Exception:raise HTTPException(503,'MongoDB unavailable')

@app.post('/employees',status_code=201)
def create_employee(body:EmployeeIn):
    d=body.model_dump()
    try:
        d['created_at']=now_utc();employees.insert_one(d)
    except DuplicateKeyError:raise HTTPException(409,'emp_code already exists')
    return public_doc(d)
@app.get('/employees')
def list_employees(department:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={'department':department} if department is not None else {}
    total=employees.count_documents(q);items=list(employees.find(q,{'_id':0}).sort('emp_code',ASCENDING).skip((page-1)*page_size).limit(page_size))
    return {'items':[public_doc(x) for x in items],'total':total,'page':page,'page_size':page_size}
@app.post('/attendance/punch-in',status_code=201)
def punch_in(body:PunchIn):
    emp=employees.find_one({'emp_code':body.emp_code})
    if not emp:raise HTTPException(404,'unknown employee')
    ts=dt_from_ms(body.punched_at) if body.punched_at is not None else now_utc()
    ts=ts.replace(microsecond=0); d=attendance_date(ts,emp)
    fields={'emp_code':body.emp_code,'date':d,'status':body.status,'punch_in':ts,'punch_out':None,'work_hours':None,'late_minutes':calc_late(ts,d,emp),'overtime_minutes':0,'half_day':False,'history':[]}
    try:logs.insert_one(fields)
    except DuplicateKeyError:raise HTTPException(409,'already punched in for this date')
    return public_doc(fields)
@app.post('/attendance/punch-out')
def punch_out(body:PunchOut):
    emp=employees.find_one({'emp_code':body.emp_code})
    if not emp:raise HTTPException(404,'unknown employee')
    ts=(dt_from_ms(body.punched_at) if body.punched_at is not None else now_utc()).replace(microsecond=0)
    d=logs.find_one({'emp_code':body.emp_code,'punch_in':{'$lte':ts}},sort=[('punch_in',DESCENDING)])
    if not d:raise HTTPException(404,'no punch-in found')
    if d.get('punch_out') is not None:raise HTTPException(409,'already punched out')
    if ts<=d['punch_in'] or ts>d['punch_in']+timedelta(hours=24):raise HTTPException(422,'punch-out must be after punch-in and within 24 hours')
    calc=calc_derived(d['punch_in'],ts,d['date'],emp)
    updated=logs.find_one_and_update({'_id':d['_id'],'punch_out':None},{'$set':{'punch_out':ts,**calc}},return_document=ReturnDocument.AFTER)
    if not updated:raise HTTPException(409,'already punched out')
    return public_doc(updated)
@app.get('/attendance')
def list_attendance(emp_code:Optional[str]=None,date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    if status is not None and status not in STATUSES:raise HTTPException(422,'invalid status')
    if date_from:
        try:date.fromisoformat(date_from)
        except:raise HTTPException(422,'invalid date_from')
    if date_to:
        try:date.fromisoformat(date_to)
        except:raise HTTPException(422,'invalid date_to')
    if date_from and date_to and date_from>date_to:raise HTTPException(422,'date_from must be <= date_to')
    q={}
    if emp_code is not None:q['emp_code']=emp_code
    if date_from or date_to:
        q['date']={}
        if date_from:q['date']['$gte']=date_from
        if date_to:q['date']['$lte']=date_to
    if status:q['status']=status
    total=logs.count_documents(q);docs=list(logs.find(q,{'_id':0}).sort([('date',DESCENDING),('emp_code',ASCENDING)]).skip((page-1)*page_size).limit(page_size))
    return {'items':[public_doc(d) for d in docs],'total':total,'page':page,'page_size':page_size}
@app.patch('/attendance/{emp_code}/{date_str}')
def regularize(emp_code:str,date_str:str,body:Regularize):
    try:
        if date.fromisoformat(date_str).isoformat()!=date_str:raise ValueError()
    except:raise HTTPException(422,'date must be YYYY-MM-DD')
    emp=employees.find_one({'emp_code':emp_code})
    if not emp:raise HTTPException(404,'unknown employee')
    old=logs.find_one({'emp_code':emp_code,'date':date_str})
    if not old:raise HTTPException(404,'attendance record not found')
    pin=dt_from_ms(body.punch_in) if 'punch_in' in body.model_fields_set else old.get('punch_in')
    pout=dt_from_ms(body.punch_out) if 'punch_out' in body.model_fields_set else old.get('punch_out')
    status=body.status if body.status is not None else old['status']
    if status in ('ABSENT','LEAVE'):
        if body.punch_in is not None or body.punch_out is not None:raise HTTPException(422,'ABSENT/LEAVE cannot include punch times')
        pin=pout=None
    elif pin is None:raise HTTPException(422,'presence status requires punch_in')
    if pin and attendance_date(pin,emp)!=date_str:raise HTTPException(422,'punch_in must match attendance date')
    if pout and (not pin or pout<=pin or pout>pin+timedelta(hours=24)):raise HTTPException(422,'invalid punch_out')
    calc=calc_derived(pin,pout,date_str,emp) if status in PRESENCE else {'late_minutes':0,'work_hours':None,'overtime_minutes':0,'half_day':False}
    new={'status':status,'punch_in':pin,'punch_out':pout,**calc}
    changes={}
    for k,v in new.items():
        ov=old.get(k)
        if ov!=v:changes[k]={'from':ov,'to':v}
    if not changes:raise HTTPException(422,'request changes nothing')
    entry={'at':now_utc(),'by':body.regularized_by,'reason':body.reason,'changes':changes}
    # Compare-and-swap protects against silently losing concurrent history updates.
    old_hist=old.get('history',[])
    updated=logs.find_one_and_update({'_id':old['_id'],'history':old_hist}, {'$set':new,'$push':{'history':entry}},return_document=ReturnDocument.AFTER)
    if not updated:raise HTTPException(409,'record changed concurrently; retry')
    return public_doc(updated)

# Analytics use MongoDB aggregation pipelines. Stored derived fields are authoritative.
def month_filter(month):
    a,b=month_bounds(month);return a.isoformat(),(b+timedelta(days=1)).isoformat()
@app.get('/analytics/employees/{emp_code}/monthly')
def employee_monthly(emp_code:str,month:str=Query(...,pattern=r'^\d{4}-(0[1-9]|1[0-2])$')):
    emp=employees.find_one({'emp_code':emp_code})
    if not emp:raise HTTPException(404,'unknown employee')
    a,b=month_bounds(month); start=max(a,date.fromisoformat(emp['joined_on'])); wd=workdays(start,b) if start<=b else 0
    lo,hi=month_filter(month)
    pipe=[{'$match':{'emp_code':emp_code,'date':{'$gte':lo,'$lt':hi}}},{'$addFields':{'_weekday':{'$dayOfWeek':{'$dateFromString':{'dateString':'$date'}}}}},{'$group':{'_id':None,'present_days':{'$sum':{'$cond':[{'$and':[{'$in':['$status',list(PRESENCE)]},{'$in':['$_weekday',[2,3,4,5,6]]}]},{'$cond':[{'$eq':[{'$ifNull':['$half_day',False]},True]},0.5,1]},0]}},'leave_days':{'$sum':{'$cond':[{'$eq':['$status','LEAVE']},1,0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}},'total_late_minutes':{'$sum':{'$ifNull':['$late_minutes',0]}},'total_overtime_minutes':{'$sum':{'$ifNull':['$overtime_minutes',0]}}}}]
    row=list(logs.aggregate(pipe));r=row[0] if row else {}
    pct=round4(r.get('present_days',0)/wd*100) if wd else None
    return {'emp_code':emp_code,'month':month,'working_days':wd,'present_days':r.get('present_days',0),'leave_days':r.get('leave_days',0),'late_count':r.get('late_count',0),'total_late_minutes':r.get('total_late_minutes',0),'total_overtime_minutes':r.get('total_overtime_minutes',0),'attendance_pct':round2(pct) if pct is not None else None}
@app.get('/analytics/departments/summary')
def department_summary(month:str=Query(...,pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),department:Optional[str]=None):
    lo,hi=month_filter(month); a,b=month_bounds(month)
    # Start from employees so employees without attendance logs still contribute to headcount.
    pipe=[{'$match':{'joined_on':{'$lte':b.isoformat()},**({'department':department} if department is not None else {})}},{'$lookup':{'from':'attendance_logs','let':{'code':'$emp_code','joined':'$joined_on'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$emp_code','$$code']},{'$gte':['$date',lo]},{'$lt':['$date',hi]},{'$gte':['$date','$$joined']}]}}},{'$addFields':{'_weekday':{'$dayOfWeek':{'$dateFromString':{'dateString':'$date'}}}}}], 'as':'logs'}},{'$unwind':{'path':'$logs','preserveNullAndEmptyArrays':True}},{'$group':{'_id':'$department','headcount':{'$addToSet':'$emp_code'},'present_days':{'$sum':{'$cond':[{'$and':[{'$in':['$logs.status',list(PRESENCE)]},{'$in':['$logs._weekday',[2,3,4,5,6]]}]},{'$cond':[{'$eq':[{'$ifNull':['$logs.half_day',False]},True]},0.5,1]},0]}},'hours':{'$push':{'$cond':[{'$and':[{'$in':['$logs.status',list(PRESENCE)]},{'$ne':[{'$ifNull':['$logs.work_hours',None]},None]}]},'$logs.work_hours',None]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$logs.late_minutes',0]},0]},1,0]}},'total_late_minutes':{'$sum':{'$ifNull':['$logs.late_minutes',0]}},'leave_count':{'$sum':{'$cond':[{'$eq':['$logs.status','LEAVE']},1,0]}},'on_duty_count':{'$sum':{'$cond':[{'$eq':['$logs.status','ON_DUTY']},1,0]}}}},{'$project':{'_id':0,'department':'$_id','headcount':{'$size':'$headcount'},'present_days':1,'avg_work_hours':{'$let':{'vars':{'h':{'$filter':{'input':'$hours','as':'h','cond':{'$ne':['$$h',None]}}}},'in':{'$cond':[{'$gt':[{'$size':'$$h'},0]},{'$round':[{'$avg':'$$h'},2]},None]}}},'late_count':1,'total_late_minutes':1,'leave_count':1,'on_duty_count':1}},{'$sort':{'department':1}}]
    rows=list(employees.aggregate(pipe));return {'month':month,'items':rows}
@app.get('/analytics/leaderboard/late')
def late_leaderboard(month:str=Query(...,pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),limit:int=Query(10,ge=1,le=50),department:Optional[str]=None):
    lo,hi=month_filter(month)
    pipe=[{'$match':{'date':{'$gte':lo,'$lt':hi},'late_minutes':{'$gt':0}}},{'$group':{'_id':'$emp_code','total_late_minutes':{'$sum':'$late_minutes'},'late_count':{'$sum':1}}},{'$lookup':{'from':'employees','localField':'_id','foreignField':'emp_code','as':'emp'}},{'$unwind':'$emp'}]
    if department is not None:pipe.append({'$match':{'emp.department':department}})
    pipe += [{'$set':{'emp_code':'$_id','name':'$emp.name','department':'$emp.department'}},{'$sort':{'total_late_minutes':-1,'emp_code':1}},{'$setWindowFields':{'sortBy':{'total_late_minutes':-1},'output':{'rank':{'$rank':{}}}}},{'$match':{'rank':{'$lte':limit}}},{'$project':{'_id':0,'rank':1,'emp_code':1,'name':1,'department':1,'total_late_minutes':1,'late_count':1}}]
    return {'month':month,'items':list(logs.aggregate(pipe))}
@app.get('/analytics/departments/{department}/trend')
def department_trend(department:str,from_date:str=Query(...,alias='from'),to_date:str=Query(...,alias='to')):
    try:a=date.fromisoformat(from_date);b=date.fromisoformat(to_date)
    except:raise HTTPException(422,'from and to must be YYYY-MM-DD')
    if b<a or (b-a).days+1>92:raise HTTPException(422,'invalid date range')
    if not employees.find_one({'department':department}):raise HTTPException(404,'unknown department')
    # MongoDB creates every calendar day, including weekends and dates with no logs.
    pipe = [
        {'$match': {'department': department, 'joined_on': {'$lte': b.isoformat()}}},
        {'$lookup': {'from': 'attendance_logs', 'let': {'code': '$emp_code'}, 'pipeline': [
            {'$match': {'$expr': {'$and': [
                {'$eq': ['$emp_code', '$$code']}, {'$gte': ['$date', a.isoformat()]}, {'$lte': ['$date', b.isoformat()]}
            ]}}}
        ], 'as': 'logs'}},
        {'$unwind': {'path': '$logs', 'preserveNullAndEmptyArrays': True}},
        {'$group': {'_id': {'date': '$logs.date', 'emp': '$emp_code'},
            'status': {'$first': '$logs.status'}, 'late': {'$first': {'$ifNull': ['$logs.late_minutes', 0]}},
            'half': {'$first': {'$ifNull': ['$logs.half_day', False]}}}},
        {'$group': {'_id': '$_id.date',
            'present_count': {'$sum': {'$cond': [{'$in': ['$status', list(PRESENCE)]}, {'$cond': ['$half', 0.5, 1]}, 0]}},
            'late_count': {'$sum': {'$cond': [{'$gt': ['$late', 0]}, 1, 0]}}}},
        {'$project': {'date': '$_id', 'present_count': 1, 'late_count': 1, '_id': 0}},
        {'$set': {'date': {'$dateFromString': {'dateString': '$date'}}}},
        {'$densify': {'field': 'date', 'range': {'step': 1, 'unit': 'day', 'bounds': [datetime.combine(a, time.min), datetime.combine(b, time.min)]}}},
        {'$set': {'date_str': {'$dateToString': {'date': '$date', 'format': '%Y-%m-%d'}},
            'is_working_day': {'$and': [{'$gte': [{'$dayOfWeek': '$date'}, 2]}, {'$lte': [{'$dayOfWeek': '$date'}, 6]}]}}},
        {'$lookup': {'from': 'employees', 'let': {'day': '$date_str'}, 'pipeline': [
            {'$match': {'department': department}}, {'$match': {'$expr': {'$lte': ['$joined_on', '$$day']}}}, {'$count': 'n'}
        ], 'as': 'hc'}},
        {'$set': {'headcount': {'$ifNull': [{'$first': '$hc.n'}, 0]},
            'present_count': {'$ifNull': ['$present_count', 0]}, 'late_count': {'$ifNull': ['$late_count', 0]}}},
        {'$set': {'attendance_rate': {'$cond': [
            {'$and': ['$is_working_day', {'$gt': ['$headcount', 0]}]},
            {'$round': [{'$divide': ['$present_count', '$headcount']}, 4]}, None
        ]}}},
        {'$setWindowFields': {'sortBy': {'date': 1}, 'output': {
            'moving_avg_7d': {'$avg': '$attendance_rate', 'window': {'documents': [-6, 0]}}
        }}},
        {'$project': {'_id': 0, 'date': '$date_str', 'is_working_day': 1, 'headcount': 1,
            'present_count': 1, 'late_count': 1, 'attendance_rate': 1, 'moving_avg_7d': 1}},
        {'$sort': {'date': 1}}
    ]
    rows=list(logs.aggregate(pipe));
    # Correct moving average output to four decimals; aggregation still performs gap filling/windowing.
    for r in rows:
        if r.get('attendance_rate') is not None:r['attendance_rate']=round4(r['attendance_rate'])
        if r.get('moving_avg_7d') is not None:r['moving_avg_7d']=round4(r['moving_avg_7d'])
    return {'department':department,'items':rows}
@app.get('/admin/explain/{endpoint}')
def explain(endpoint:str,emp_code:Optional[str]=None,month:Optional[str]=None,department:Optional[str]=None,limit:int=Query(10,ge=1,le=50),date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,from_date:Optional[str]=Query(None,alias='from'),to_date:Optional[str]=Query(None,alias='to'),page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    if endpoint not in {'attendance_list','employee_monthly','department_summary','late_leaderboard','department_trend'}:raise HTTPException(422,'unknown endpoint')
    if endpoint=='attendance_list':
        q={}
        if emp_code:q['emp_code']=emp_code
        if status:q['status']=status
        if date_from or date_to:
            q['date']={}
            if date_from:q['date']['$gte']=date_from
            if date_to:q['date']['$lte']=date_to
        exp=db.command('explain',{'find':'attendance_logs','filter':q,'sort':{'date':-1,'emp_code':1},'skip':(page-1)*page_size,'limit':page_size},verbosity='executionStats');return {'endpoint':endpoint,'collection':'attendance_logs','explain':exp}
    if not month:raise HTTPException(422,'month is required')
    lo,hi=month_filter(month)
    if endpoint=='employee_monthly':
        if not emp_code:raise HTTPException(422,'emp_code is required')
        p=[{'$match':{'emp_code':emp_code,'date':{'$gte':lo,'$lt':hi}}},{'$group':{'_id':'$emp_code','n':{'$sum':1}}}];coll='attendance_logs'
    elif endpoint=='department_summary':
        p=[{'$match':{'date':{'$gte':lo,'$lt':hi}}},{'$group':{'_id':'$emp_code','n':{'$sum':1}}}];coll='attendance_logs'
    elif endpoint=='late_leaderboard':
        p=[{'$match':{'date':{'$gte':lo,'$lt':hi},'late_minutes':{'$gt':0}}},{'$group':{'_id':'$emp_code','n':{'$sum':'$late_minutes'}}}];coll='attendance_logs'
    else:
        if not department or not from_date or not to_date:raise HTTPException(422,'department, from and to are required')
        try:a=date.fromisoformat(from_date);b=date.fromisoformat(to_date)
        except:raise HTTPException(422,'invalid date range')
        if b<a or (b-a).days+1>92:raise HTTPException(422,'invalid date range')
        p=[{'$match':{'department':department,'joined_on':{'$lte':to_date}}},{'$lookup':{'from':'attendance_logs','localField':'emp_code','foreignField':'emp_code','as':'logs'}}];coll='employees'
    exp=db.command({'aggregate': coll, 'pipeline': p, 'cursor': {}}, explain=True);return {'endpoint':endpoint,'collection':coll,'explain':exp}
