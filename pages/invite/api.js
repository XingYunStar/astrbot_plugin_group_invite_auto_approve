/**
 * AstrBot 插件页 bridge 的最小封装。
 *
 * 页面运行在 ``sandbox="allow-scripts allow-forms allow-downloads"`` 的 iframe 中，
 * 没有 allow-same-origin，因此不能直接 fetch / 使用 localStorage，
 * 所有后端请求都必须走 ``window.AstrBotPluginPage``（postMessage）桥。
 */
export function createApi(bridge) {
  if (!bridge || typeof bridge.apiGet !== "function") {
    throw new Error("AstrBotPluginPage bridge 不可用");
  }

  function unwrap(response) {
    if (response && typeof response === "object" && "ok" in response) {
      if (!response.ok) {
        throw new Error(response.message || "请求失败");
      }
      return "data" in response ? response.data : response;
    }
    return response;
  }

  return {
    /** GET 请求，返回值已解包为 data。 */
    async get(endpoint, params) {
      return unwrap(await bridge.apiGet(endpoint, params || {}));
    },
    /** POST 请求，返回值已解包为 data。 */
    async post(endpoint, body) {
      return unwrap(await bridge.apiPost(endpoint, body || {}));
    },
    raw: bridge,
  };
}
