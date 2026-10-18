  // Block SVCB/HTTPS DNS questions in every configured resolver path.
  const blockTypes = server => {
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
