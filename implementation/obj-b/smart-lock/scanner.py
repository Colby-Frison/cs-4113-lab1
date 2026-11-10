"""Source-pattern checks, not control-flow or exploitability."""
import re

def clean_source(source):
    chars=list(source);i=0

    while i<len(source):
        end=None
        if source.startswith('//',i):
            end=source.find('\n',i)

            if end<0:end=len(source)

        elif source.startswith('/*',i):
            end=source.find('*/',i+2);end=len(source) if end<0 else end+2

        elif source[i] in ('"',"'"):
            quote=source[i];end=i+1
            while end<len(source):
                if source[end]=='\\':end+=2;continue
                if source[end]==quote:end+=1;break
                end+=1
            end=min(end,len(source))

        if end is None:i+=1;continue
        for j in range(i,end):
            if chars[j]!='\n':chars[j]=' '

        i=end
    return ''.join(chars)

def find_candidates(code,check):
    findings=[]
    unlock=re.compile(r'\b(unlock|unlockDoor|unlock_door|setUnlocked)\s*\(')
    auth=re.compile(r'\b(auth\w*|\w*permission\w*|verify\w*|isAllowed|is_allowed)\b',re.I)
    fresh=re.compile(r'\b(\w*nonce\w*|\w*sequence\w*|\w*counter\w*|\w*timestamp\w*)\b',re.I)

    def add(m,rule,reason):
        findings.append(dict(rule=rule,line=code.count('\n',0,m.start())+1,symbol=m.group(1),reason=reason))
    
    if check=='input_bounds':
        for m in re.finditer(r'\b(gets|strcpy|strcat|sprintf)\s*\(',code):
            add(m,'unbounded_input_call','Review unbounded input/copy/format call and actual buffer sizes.')
    
    elif check=='authorization':
        for m in unlock.finditer(code):
            line=code.count('\n',0,m.start());context='\n'.join(code.splitlines()[max(0,line-12):line+1])
            if not auth.search(context):add(m,'unlock_auth_review','No nearby authorization identifier, inspect callers and control flow.')
    
    elif check=='replay':
        m=unlock.search(code)
        if m and not fresh.search(code):add(m,'unlock_freshness_review','No local freshness identifier, inspect protocol and callers.')
    else:raise ValueError('unknown check '+str(check))
    return findings
