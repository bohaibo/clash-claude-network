"""Audit early DIRECT scope and additional ingress without changing source profiles."""
import ipaddress,re
from checks import require

LOCAL_NETWORKS=tuple(ipaddress.ip_network(value) for value in (
 '0.0.0.0/8','10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','169.254.0.0/16',
 '172.16.0.0/12','192.168.0.0/16','224.0.0.0/4','255.255.255.255/32','::/127','fc00::/7','fe80::/10','ff00::/8'))
REVERSE_NETWORKS=LOCAL_NETWORKS+tuple(ipaddress.ip_network(value) for value in (
 '192.0.2.0/24','198.51.100.0/24','203.0.113.0/24','2001:db8::/32'))
# Reviewed local service/router names, not a remotely extensible public suffix list.
LOCAL_NAMES=('lan','local','internal','localhost','localdomain','example','invalid','test','home.arpa',
 'asusrouter.com','oasisauth.h3c.com','localhost.weixin.qq.com','localhost.work.weixin.qq.com',
 'local.adguard.org','tplinkwifi.net','plex.direct','ts.net','localhost.ptlogin2.qq.com',
 'localhost.sec.qq.com','kis.v2.scr.kaspersky-labs.com')

def local_network(network):
 return any(network.version==item.version and network.subnet_of(item) for item in LOCAL_NETWORKS)

def normalize_lan_cidrs(values,audit):
 result=[]
 for value in values:
  network=ipaddress.ip_network(value,strict=False)
  require(network.prefixlen>0,'LAN source contains a whole-network rule; review required')
  pieces=[]
  for allowed in LOCAL_NETWORKS:
   if allowed.version!=network.version or not network.overlaps(allowed):continue
   pieces.append(str(network if network.subnet_of(allowed) else allowed))
  pieces=list(dict.fromkeys(pieces))
  if pieces!=[str(network)]:audit.append({'kind':'non-lan-cidr','original':str(network),'retained':pieces})
  result.extend(pieces)
 return list(dict.fromkeys(result))

def lan_domain(value,policy):
 require(isinstance(value,str),'Invalid LAN domain')
 domain=value.removeprefix('+.').removeprefix('*.').rstrip('.').lower()
 require(bool(re.fullmatch(r'[a-z0-9_-]+(?:\.[a-z0-9_-]+)*',domain)),'Unknown LAN domain syntax')
 protected=policy['claude']+policy['extraSuffix']+policy['extraExact']
 require(not any(domain==host or domain.endswith('.'+host) or host.endswith('.'+domain) for host in protected),
         'LAN domain overlaps a protected public domain; review required')
 names=list(LOCAL_NAMES)
 names.extend(rule.split(',')[1] for rule in policy['legacyLan'] if rule.startswith(('DOMAIN,','DOMAIN-SUFFIX,')))
 if any(domain==name or domain.endswith('.'+name) for name in names):return
 # Reverse zones must describe a reviewed local address range, not all .arpa.
 try:
  if domain.endswith('.in-addr.arpa'):
   parts=domain.removesuffix('.in-addr.arpa').split('.')
   require(1<=len(parts)<=4,'Invalid reverse zone')
   address='.'.join(list(reversed(parts))+['0']*(4-len(parts)))
   network=ipaddress.ip_network(address+'/'+str(len(parts)*8))
   if any(network.version==item.version and network.subnet_of(item) for item in REVERSE_NETWORKS):return
  if domain.endswith('.ip6.arpa'):
   parts=domain.removesuffix('.ip6.arpa').split('.')
   require(1<=len(parts)<=32 and all(re.fullmatch('[0-9a-f]',part) for part in parts),'Invalid reverse zone')
   number=''.join(reversed(parts)).ljust(32,'0')
   network=ipaddress.IPv6Network((int(number,16),len(parts)*4))
   if any(network.version==item.version and network.subnet_of(item) for item in REVERSE_NETWORKS):return
 except (ValueError,TypeError):pass
 raise AssertionError('Unreviewed public domain in LAN source; source preserved for review')

def audit_lan_provider(provider,policy,audit=None):
 require(provider.get('type')=='inline' and isinstance(provider.get('payload'),list),'LAN provider must be frozen inline')
 behavior=provider.get('behavior');require(behavior in ('classical','domain','ipcidr'),'Unknown LAN provider behavior')
 cleaned=[];changes=[] if audit is None else audit
 for value in provider['payload']:
  require(isinstance(value,str),'Invalid LAN provider item')
  parts=value.split(',')
  if behavior=='ipcidr':cleaned.extend(normalize_lan_cidrs([value],changes));continue
  if behavior=='domain':lan_domain(value,policy);cleaned.append(value);continue
  if parts[0] in ('IP-CIDR','IP-CIDR6'):
   require(len(parts) in (2,3) and (len(parts)==2 or parts[2]=='no-resolve'),'Unknown classical LAN rule')
   for network in normalize_lan_cidrs([parts[1]],changes):cleaned.append(parts[0]+','+network+(',no-resolve' if len(parts)==3 else ''))
  else:
   require(len(parts)==2 and parts[0] in ('DOMAIN','DOMAIN-SUFFIX'),'Non-LAN rule in LAN provider; review required')
   lan_domain(parts[1],policy);cleaned.append(value)
 return dict(provider,payload=list(dict.fromkeys(cleaned)))

def audit_ingress(config,group):
 for field in ('listeners','tunnels'):
  entries=config.get(field,[])
  require(isinstance(entries,list),'Invalid additional ingress configuration')
  for entry in entries:
   if field=='tunnels' and isinstance(entry,str):
    parts=entry.split(',');require(len(parts)==4,'Unknown tunnel syntax')
    entry={'network':parts[0].split('/'),'address':parts[1],'target':parts[2],'proxy':parts[3]}
   require(isinstance(entry,dict) and entry.get('proxy')==group and not any(k in entry for k in ('rule','rules','sub-rules')),
           'Additional ingress can bypass pinned routing; source preserved for review')
   if field=='listeners':
    require(entry.get('type') in ('http','socks','mixed') and entry.get('listen') in ('127.0.0.1','::1'),
            'Additional listener must be a reviewed loopback proxy')
   else:
    require(bool(re.fullmatch(r'(?:127\.0\.0\.1|\[::1\]):[0-9]+',str(entry.get('address','')))),
            'Additional tunnel must listen on loopback')
    network=entry.get('network',[]);network=[network] if isinstance(network,str) else network
    require(bool(network) and isinstance(network,list) and set(network)<= {'tcp','udp'},'Unknown tunnel network')
