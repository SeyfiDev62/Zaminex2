import os

from django.core.wsgi import get_wsgi_application

from apps.common.staticfiles import static_files_handler

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# ``get_wsgi_application()`` runs first so Django is set up before the handler
# reads ``settings.DEBUG``. The wrapper is a no-op under ``DEBUG`` — see
# ``apps/common/staticfiles.py``.
application = static_files_handler(get_wsgi_application())
