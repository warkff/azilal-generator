# زر "تشغيل المحرك" — Cloudflare Worker

1. أنشئ حساب Cloudflare مجاني (dash.cloudflare.com) — بلا كارطة.
2. Workers & Pages → Create Worker → الصق محتوى worker.js → Deploy.
3. في صفحة الـ Worker: Settings → Variables → Add variable (اختر Secret):
   - GH_PAT = توكن GitHub محدود: github.com → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token → صلاحية "Codespaces" (Read and write) فقط. لا تشارك التوكن مع أي شخص ولا تضعه في الموقع.
   - CODESPACES_NAME = legendary-umbrella-xj46wvv5w99399pw
4. انسخ رابط الـ Worker (مثلا https://azilal-wake.xxx.workers.dev) وأرسله لـ Muse — سيضعه في الموقع ويعيد نشر الموقع.
