<div dir="rtl" style="font-family: 'Courier New', Courier, monospace;">
# گزارش ممیزی: کرش کانتینر سندباکس

## خلاصه

کانتینر سندباکس بلافاصله پس از شروع کرش می‌شود و قبل از اجرای `exec_run` متوقف شده است. خطا:

```
docker.errors.APIError: 500 Server Error for .../exec: Internal Server Error
("can only create exec sessions on running containers: container state improper")
```

## علت ریشه‌ای

فایل `docker/sandbox/Dockerfile` دارای دستور پیش‌فرض زیر است:

```dockerfile
CMD ["/bin/bash"]
```

وقتی کانتینر با پارامتر `detach=True` شروع می‌شود (بدون ترمینال تعاملی متصل)، `bash` بلافاصله خارج می‌شود چون stdin بسته است. وضعیت کانتینر به `exited` تغییر می‌کند و وقتی `clone_repo_in_container` تلاش می‌کند با `exec_run` دستور git clone را اجرا کند، خطای «can only create exec sessions on running containers» دریافت می‌کند.

## زمان‌بندی وقوع

```
07:41:04,398 - Container started
07:41:04,432 - Status → cloning
07:41:04,469 - Failed to start sandbox container (exec_run fails)
```

فقط ۷۱ میلی‌ثانیه بین شروع کانتینر و خطا فاصله بود — کانتینر بلافاصله خارج شده بود.

## اصلاح

تغییر `CMD` در `docker/sandbox/Dockerfile` از:
```dockerfile
CMD ["/bin/bash"]
```

به:
```dockerfile
CMD ["bash", "-c", "while true; do sleep 86400; done"]
```

این دستور کانتینر را زنده نگه می‌دارد تا `exec_run` بتواند دستورات را داخل آن اجرا کند. از `tail -f /dev/null` استفاده نشد چون ممکن است در تصاویر مینیمال وجود نداشته باشد.

## سایر مشاهدات

### اتصال کالبک ناموفق

```
apps.ingestion.callback: Callback for task 10 returned 401 (attempt 1/3)
apps.ingestion.callback: Callback for task 9 failed with ... Connection to api.github.com timed out
```

- **Task 10**: کالبک به `https://api.github.com/repos/javadib/jiffy_gateway/issues` با 401 رد شد — احتمالاً توکن HMAC یا `callback_secret` نادرست است
- **Task 9**: کالبک با خطای timeout شکست خورد — اتصال شبکه به GitHub برقرار نبود (ممکن است proxy مشکل داشته باشد)

### آزمون‌ها

- ۴۷ آزمون اجرایی همگی رد شدند (قبل و بعد از اصلاح)
- ۱۰ آزمون از قبل شکست خورده در `test_views.py` و `test_auth.py` وجود دارد (مشکل در `verify_ingest_token`) — مرتبط با این task نیست

## فایل‌های تغییر یافته

| فایل | تغییر |
|------|-------|
| `docker/sandbox/Dockerfile` | CMD از `/bin/bash` به حلقه sleep تغییر کرد |

## نتیجه‌گیری

مشکل اصلی حل شده است. پس از rebuild تصویر سندباکس (`docker build -t jiffy-sandbox:1.0.0 .`) کانتینر زنده می‌ماند و `exec_run` می‌تواند دستورات را اجرا کند.

</div>
