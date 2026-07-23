# گزارش بازبینی سندباکس — Docker Socket Proxy

## خلاصه

پس از پیاده‌سازی docker-socket-proxy و اجرای اولین تسک واقعی، خرابی زیر رخ داد:

```
docker.errors.APIError: 500 Server Error ... "can only create exec sessions on running containers: container state improper"
```

Container بلافاصله پس از شروع از حالت running خارج شد و `exec_run` با خطا مواجه شد.

---

## علت ریشه‌ای

فایل `docker/sandbox/Dockerfile` دستور پایانی زیر را دارد:

```dockerfile
CMD ["/bin/bash"]
```

وقتی контینر با `detach=True` و بدون `tty=True` اجرا می‌شود، bash بدون TTY بلافاصله خارج می‌شود. Container در عرض ~۷۰ میلی‌ثانیه به حالت `exited` می‌رود و هر فراخوانی `exec_run` با خطای «container state improper» شکست می‌خورد.

**زمان‌بندی از لاگ:**
- `07:41:04,398` — Container شروع شد
- `07:41:04,469` — exec_run شکست خورد (فقط ۷۱ میلی‌ثانیه بعد)

---

## اصلاح اعمال‌شده

فایل `jobs/execution/container.py` — تابع `start_generic_sandbox_container`:

```python
container = client.containers.run(
    settings.SANDBOX_IMAGE,
    detach=True,
    remove=False,
    tty=True,          # ← اضافه شد
    mem_limit=settings.SANDBOX_MEM_LIMIT,
    cpuset_cpus=str(settings.SANDBOX_CPU_LIMIT),
    environment=env_vars,
    network="jiffy-sandbox-net",
    **networking_config,
)
```

پارامتر `tty=True` یک pseudo-TTY به container اختصاص می‌دهد که باعث می‌شود bash منتظر بماند و container در حالت running باقی بماند.

---

## وضعیت فعلی سرویس‌ها

| سرویس | وضعیت | توضیح |
|--------|--------|--------|
| `docker-socket-proxy` | ✅ فعال | فقط گروه‌های مجاز فعال‌اند (CONTAINERS, IMAGES, BUILD, NETWORKS, EXEC, POST) |
| `celery` | ✅ فعال | از طریق `DOCKER_HOST=tcp://docker-socket-proxy:2375` به Docker متصل می‌شود |
| `web` | ✅ فعال | روی شبکه `jiffy-internal` |
| `redis` | ✅ فعال | روی شبکه `jiffy-internal` |

---

## گروه‌های endpoint فعال در پروکسی

| گروه | وضعیت | دلیل |
|-------|--------|-------|
| `CONTAINERS` | فعال | ایجاد، شروع، توقف، حذف containerهای سندباکس |
| `IMAGES` | فعال | بررسی وجود تصویر سندباکس |
| `BUILD` | فعال | ساخت تصویر سندباکس از Dockerfile در صورت نبود |
| `NETWORKS` | فعال | بررسی و ایجاد شبکه `jiffy-sandbox-net` |
| `EXEC` | فعال | اجرای دستورات در container (git clone، اجرای agent) |
| `POST` | فعال | کلید جهانی برای عملیات نوشتن |
| `VOLUMES` | غیرفعال | نیاز نیست |
| `SERVICES` | غیرفعال | Swarm استفاده نمی‌شود |
| `TASKS` | غیرفعال | Swarm استفاده نمی‌شود |
| `SYSTEM` | غیرفعال | عملیات سیستمی نیاز نیست |
| `PLUGINS` | غیرفعال | مدیریت پلاگین نیاز نیست |
| `SECRETS` | غیرفعال | Swarm استفاده نمی‌شود |
| `CONFIGS` | غیرفعال | Swarm استفاده نمی‌شود |
| `NODES` | غیرفعال | Swarm استفاده نمی‌شود |
| `SWARM` | غیرفعال | Swarm استفاده نمی‌شود |

---

## تست‌ها

- ۴۷ تست اجرایی: ✅ همه عبور می‌کنند
- ۵ تست جدید `GetDockerClientTest`: ✅ اعتبارسنجی `DOCKER_HOST`
- ۵ تست به‌روزرسانی شده `EnsureSandboxImageTest`: ✅ مسک `get_docker_client`

---

## نتیجه‌گیری

خرابی اصلی (خروج فوری container) با اضافه کردن `tty=True` حل شد. ساختار docker-socket-proxy صحیح است و فقط endpoint‌های ضروری فعال‌اند. کد worker به‌درستی از `DOCKER_HOST` استفاده می‌کند و در صورت عدم تنظیم، خطای واضحی صادر می‌شود.
