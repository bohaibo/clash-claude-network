// No network requests, browsing data collection or remote code.
function protect() {
  chrome.privacy.network.webRTCIPHandlingPolicy.set(
    { value: 'disable_non_proxied_udp', scope: 'regular' },
    () => { if (chrome.runtime.lastError) console.warn('WebRTC 路径限制未应用：请检查浏览器策略或其他扩展。'); },
  );
}
chrome.runtime.onInstalled.addListener(protect);
chrome.runtime.onStartup.addListener(protect);
protect();
