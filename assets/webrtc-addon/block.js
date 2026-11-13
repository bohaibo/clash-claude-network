// Runs at document_start in each matching frame's MAIN world.
// Page-level protection is not a browser-kernel disable switch.
(() => {
  const block = (target, name, value) => {
    try { Object.defineProperty(target, name, { value, configurable: false, writable: false }); }
    catch { /* A browser-owned non-configurable property may not be replaceable. */ }
  };
  function blockedPeerConnection() {
    throw new DOMException('WebRTC 已由本地保护扩展禁用', 'NotAllowedError');
  }
  for (const name of ['RTCPeerConnection', 'webkitRTCPeerConnection', 'mozRTCPeerConnection']) {
    block(globalThis, name, blockedPeerConnection);
  }
})();
