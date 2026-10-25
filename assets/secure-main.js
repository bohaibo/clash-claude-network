function secureMain(config,data,node,group) {

  if (!(config.proxies || []).some(p => p.name === node)) throw new Error('Selected node missing: refuse configuration');
  // Also runs when the native client reapplies an updated subscription.
  for (const field of ['listeners','tunnels']) {
    const entries = config[field] || [];
    if (!Array.isArray(entries)) throw new Error('Invalid additional ingress');
    for (let entry of entries) {
      if (field === 'tunnels' && typeof entry === 'string') {
        const parts = entry.split(',');
        if (parts.length !== 4) throw new Error('Unknown tunnel syntax');
        entry = {network:parts[0].split('/'),address:parts[1],proxy:parts[3]};
      }
      if (!entry || entry.proxy !== group || ['rule','rules','sub-rules'].some(k => k in entry))
        throw new Error('Additional ingress can bypass pinned routing; review source');
      if (field === 'listeners') {
        if (!['http','socks','mixed'].includes(entry.type) || !['127.0.0.1','::1'].includes(entry.listen))
          throw new Error('Additional listener must be a reviewed loopback proxy');
      } else {
        const network = typeof entry.network === 'string' ? [entry.network] : entry.network;
        if (!/^(?:127\.0\.0\.1|\[::1\]):[0-9]+$/.test(entry.address || '') ||
            !Array.isArray(network) || !network.length || network.some(n => !['tcp','udp'].includes(n)))
          throw new Error('Additional tunnel must use reviewed loopback routing');
      }
    }
  }
  config.mode = 'rule';
  // Keep existing groups untouched: this project only guarantees that its own pinned
  // group has no DIRECT/REJECT. Rebuilding every subscription-owned group would
  // rewrite structure the user did not authorize changing.
  config['proxy-groups'] = (config['proxy-groups'] || []).filter(g => g.name !== group);
  config['proxy-groups'].push({name:group,type:'select',proxies:[node]});
  config['rule-providers'] = config['rule-providers'] || {};
  for (const provider of Object.values(config['rule-providers'])) if (provider.type === 'http') provider.proxy = group;
  for (const provider of Object.values(config['proxy-providers'] || {})) if (provider.type === 'http') provider.proxy = group;
  config['rule-providers']['ACL-LocalAreaNetwork'] = {type:'inline',behavior:'classical',payload:data.legacyLan};
  config['rule-providers']['strict-cn-domain'] = {type:'inline',behavior:'domain',payload:data.domains};
  config['rule-providers']['strict-cncidr'] = {type:'inline',behavior:'ipcidr',payload:data.ips};
  config['rule-providers']['strict-private-domain'] = {type:'inline',behavior:'domain',payload:data.privateDomains};
  config['rule-providers']['strict-lancidr'] = {type:'inline',behavior:'ipcidr',payload:data.lanCidrs};
  delete config['prepend-rules']; delete config['append-rules'];
  config.rules = [
    ...data.lan,
    'RULE-SET,strict-private-domain,DIRECT',
    'RULE-SET,strict-lancidr,DIRECT,no-resolve',
    'IP-CIDR6,::/0,REJECT,no-resolve',
    ...data.claudeProcesses.map(p => 'PROCESS-NAME,'+p+','+group),
    ...data.claudeIpv4.map(ip => 'IP-CIDR,'+ip+','+group+',no-resolve'),
    ...data.claude.map(d => 'DOMAIN-SUFFIX,'+d+','+group),
    ...data.extraSuffix.map(d => 'DOMAIN-SUFFIX,'+d+','+group),
    ...data.extraExact.map(d => 'DOMAIN,'+d+','+group),
    'DOMAIN,servd-anthropic-website.b-cdn.net,'+group,
    'DOMAIN,challenges.cloudflare.com,'+group,
    'RULE-SET,strict-cn-domain,DIRECT',
    'RULE-SET,strict-cncidr,DIRECT,no-resolve',
    'GEOIP,CN,DIRECT,no-resolve',
    'MATCH,'+group
  ];
  const overseas = ['https://1.1.1.1/dns-query#'+group,'https://8.8.8.8/dns-query#'+group];
  const domestic = ['https://223.5.5.5/dns-query#DIRECT','https://223.6.6.6/dns-query#DIRECT'];
  const policy = {};
  for (const d of [...data.claude,...data.extraSuffix]) policy['+.'+d] = overseas;
  for (const d of data.extraExact) policy[d] = overseas;
  policy['rule-set:strict-private-domain'] = ['system'];
  policy['challenges.cloudflare.com'] = overseas;
  policy['servd-anthropic-website.b-cdn.net'] = overseas;
  policy['rule-set:strict-cn-domain'] = domestic;
  policy['+.lan'] = ['system']; policy['+.local'] = ['system'];
  config.dns = {
    enable:true,listen:'127.0.0.1:5334',ipv6:false,
    'enhanced-mode':'fake-ip','fake-ip-range':'198.18.0.1/16',
    'fake-ip-filter':['rule-set:strict-private-domain','*.lan','+.local','localhost'],
    'default-nameserver':['223.5.5.5','223.6.6.6'],
    'proxy-server-nameserver':domestic,
    nameserver:overseas,'nameserver-policy':policy,
    'direct-nameserver':domestic,'direct-nameserver-follow-policy':false,
    'respect-rules':true,'use-system-hosts':false
  };
  config.ipv6 = false;
  config.tun = {
    ...(config.tun || {}),enable:true,stack:'mixed',
    'auto-route':true,'auto-detect-interface':true,'strict-route':true,
    'dns-hijack':['any:53','tcp://any:53'],
    'inet6-address':[]
  };
  config.sniffer = {
    enable:true,'force-dns-mapping':true,'parse-pure-ip':true,'override-destination':true,
    sniff:{TLS:{ports:[443,8443]},HTTP:{ports:[80,'8080-8880'],'override-destination':true},QUIC:{ports:[443,8443]}},
    'skip-domain':['+.lan','+.local']
  };
  // Block SVCB/HTTPS DNS questions in every configured resolver path.
  // Only real resolver URLs accept query flags. Special values such as "system"
  // or "dhcp://..." must be left untouched; appending "#disable-qtype-64=true"
  // turns them into an invalid server string and breaks private/LAN lookups.
  const blockTypes = server => {
    if (!/^(?:https?|tls|quic|h3):\/\//i.test(server)) return server;
    let out = server;
    for (const type of [64,65]) {
      const flag = 'disable-qtype-'+type+'=true';
      if (!out.includes(flag)) out += (out.includes('#') ? '&' : '#')+flag;
    }
    return out;
  };
  for (const key of ['default-nameserver','nameserver','fallback','proxy-server-nameserver','direct-nameserver']) {
    if (config.dns[key]) config.dns[key] = config.dns[key].map(blockTypes);
  }
  for (const key of Object.keys(config.dns['nameserver-policy'])) {
    const value = config.dns['nameserver-policy'][key];
    config.dns['nameserver-policy'][key] = Array.isArray(value) ? value.map(blockTypes) : blockTypes(value);
  }
  return config;
}
