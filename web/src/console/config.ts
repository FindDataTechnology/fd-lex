// 控制台侧部署配置。读法与 src/api/config.ts 的 VITE_API_BASE 同一惯例：
// 构建期 import.meta.env 覆盖，未配置时回落默认值。
//
// 萬星门面地址是部署事实（本地/预发/生产/客户私有部署各不相同），不写死在源码里。
// 默认值 = 萬星独立成仓（fd-wanxing）之前的线上地址，与旧常量逐字一致。
export const WANXING_ORIGIN = (
  String(import.meta.env.VITE_WANXING_ORIGIN ?? '').trim() || 'https://wanxing.finddatatech.cloud'
).replace(/\/$/, '')
