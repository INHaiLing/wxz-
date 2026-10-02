"""CI smoke: real Gunicorn, production checks, ephemeral configuration only."""
from pathlib import Path
import base64
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen


base = Path(__file__).resolve().parents[1]
if os.name == "nt":
    raise SystemExit("生产 WSGI 冒烟验证需 Linux。")
with tempfile.TemporaryDirectory() as folder:
    env = dict(os.environ)
    env.update({
        "DJANGO_SETTINGS_MODULE":"config.production_settings", "DJANGO_DEBUG":"false",
        "DJANGO_SECRET_KEY":secrets.token_urlsafe(64),
        "DJANGO_ALLOWED_HOSTS":"ci.example.invalid", "DJANGO_CSRF_TRUSTED_ORIGINS":"https://ci.example.invalid",
        "STUDENT_SESSION_ENCRYPTION_KEYS":base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "DB_ENGINE":"postgresql", "DB_NAME":env.get('TEST_DB_NAME','chinese_study_ci'),
        "DB_USER":env.get('TEST_DB_USER','ci_runner'), "DB_PASSWORD":env.get('TEST_DB_PASSWORD',''),
        "DB_HOST":env.get('TEST_DB_HOST','127.0.0.1'), "DB_PORT":env.get('TEST_DB_PORT','5432'),
        "DJANGO_STATIC_ROOT":folder+'/static', "WECHAT_APP_ID":"", "WECHAT_APP_SECRET":"",
        "VIRTUAL_PAYMENT_ENABLED":"false",
    })
    subprocess.run([sys.executable,'manage.py','check','--deploy','--fail-level','WARNING'],cwd=base,env=env,check=True)
    subprocess.run([sys.executable,'manage.py','collectstatic','--noinput','--verbosity','0'],cwd=base,env=env,check=True)
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1',0)); port=reserved.getsockname()[1]
    with open(Path(folder)/'server.log','w+b') as logs:
        process=subprocess.Popen([sys.executable,'-m','gunicorn','config.wsgi:application',
                                  '--config','deploy/gunicorn.conf.py','--bind',f'127.0.0.1:{port}'],
                                 cwd=base,env=env,stdout=logs,stderr=logs)
        try:
            for attempt in range(40):
                if process.poll() is not None:
                    raise RuntimeError('Gunicorn 启动失败。')
                try:
                    request=Request(f'http://127.0.0.1:{port}/healthz?token=SECRET_LOG_PROBE',
                                    headers={'Host':'ci.example.invalid','X-Forwarded-Proto':'https'})
                    with urlopen(request,timeout=2) as response:
                        assert response.status==200 and response.headers.get('X-Request-ID')
                    break
                except OSError:
                    time.sleep(.25)
            else:
                raise RuntimeError('Gunicorn 就绪超时。')
            request=Request(f'http://127.0.0.1:{port}/readyz',headers={'Host':'ci.example.invalid','X-Forwarded-Proto':'https'})
            with urlopen(request,timeout=5) as response:
                assert response.status==200
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        logs.seek(0)
        assert b'SECRET_LOG_PROBE' not in logs.read()
print('生产检查、静态收集、真实 Gunicorn／数据库就绪与日志脱敏冒烟通过。')
