"""Positive-control test against the installed Mihomo; no TUN or live config changes."""
from checks import require
from pathlib import Path
import json,os,socket,struct,subprocess,threading,time
import yaml
from client import LOCAL,executables,write
from verify import query

def main():
 work=LOCAL/'isolated-dns-test';work.mkdir(exist_ok=True)
 upstream=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);upstream.bind(('127.0.0.1',0));upstream.settimeout(.2)
 upstream_port=upstream.getsockname()[1]
 stop=threading.Event();seen=[]
 def server():
  while not stop.is_set():
   try:packet,address=upstream.recvfrom(4096)
   except socket.timeout:continue
   except OSError:return
   offset=12
   while packet[offset]:offset+=packet[offset]+1
   offset+=1;kind,_=struct.unpack('!HH',packet[offset:offset+4]);question=packet[12:offset+4];seen.append(kind)
   if kind==1:data=socket.inet_aton('203.0.113.7')
   elif kind in (64,65):data=struct.pack('!H',1)+b'\0'  # Service form, target root, valid empty SvcParams.
   else:data=None
   answer=b'' if data is None else b'\xc0\x0c'+struct.pack('!HHIH',kind,1,60,len(data))+data
   response=packet[:2]+struct.pack('!HHHHH',0x8180,1,int(data is not None),0,0)+question+answer
   upstream.sendto(response,address)
 thread=threading.Thread(target=server,daemon=True);thread.start()
 listener=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);listener.bind(('127.0.0.1',0));port=listener.getsockname()[1];listener.close()
 core=executables()[1]
 config={'mode':'rule','ipv6':False,'mixed-port':0,'port':0,'socks-port':0,'external-controller':'',
  'external-controller-pipe':'','tun':{'enable':False},'rules':['MATCH,DIRECT'],
  'dns':{'enable':True,'listen':'127.0.0.1:'+str(port),'ipv6':False,'enhanced-mode':'redir-host',
    'default-nameserver':['127.0.0.1:'+str(upstream_port)],
    'nameserver':['udp://127.0.0.1:'+str(upstream_port)+'#disable-qtype-64=true&disable-qtype-65=true']}}
 path=work/'config.yaml';path.write_text(yaml.safe_dump(config),encoding='utf-8')
 stdout=open(work/'core-output.txt','wb');process=None
 report={'success':False,'live_configuration_changed':False,'tun_enabled':False,'external_upstream_used':False}
 try:
  controls=[]
  for kind in (64,65):
   result=query('127.0.0.1',upstream_port,kind,host='type-check.example.com')
   require(result['rcode']==0 and result['answer_count']==1,'Synthetic positive control failed')
   controls.append({'qtype':kind,'unfiltered_answer_count':result['answer_count']})
  process=subprocess.Popen([core,'-d',str(work),'-f',str(path)],stdout=stdout,stderr=subprocess.STDOUT)
  deadline=time.monotonic()+15
  while True:
   try:
    value=query('127.0.0.1',port,1,host='type-check.example.com')
    require(value['answer_count']==1,'检查未通过');break
   except Exception:
    if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Isolated DNS listener did not become ready; see local output')
    time.sleep(.1)
  before=len([t for t in seen if t in (64,65)])
  filtered=[query('127.0.0.1',port,kind,tcp,host='type-check.example.com') for kind in (64,65) for tcp in (False,True)]
  require(all(r['passed'] for r in filtered),'Installed core did not filter a known-positive SVCB/HTTPS response')
  require(len([t for t in seen if t in (64,65)])==before,'Blocked qtype unexpectedly reached upstream')
  report.update({'success':True,'positive_controls':controls,'filtered_results':filtered,'blocked_questions_reached_upstream':False})
 finally:
  if process is not None:
   process.terminate()
   try:process.wait(timeout=5)
   except subprocess.TimeoutExpired:process.kill();process.wait()
  stdout.close();stop.set();upstream.close();thread.join(timeout=1)
  write(LOCAL/'dns-filter-positive-control.json',report);print(json.dumps(report))
if __name__=='__main__':main()
