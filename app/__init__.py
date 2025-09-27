# Disable matplotlib display
import matplotlib
matplotlib.use('Agg')
del matplotlib

import logging, os, sys
import os
from flask import Flask
from flask_bootstrap import Bootstrap
from werkzeug.middleware.proxy_fix import ProxyFix

from . import config as config_module

# Create flask application
app = Flask(__name__)

log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_name, logging.INFO)

# ensure the Flask app logger emits to stdout at desired level
app.logger.setLevel(log_level)
if not app.logger.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setLevel(log_level)
    h.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s: %(message)s'
    ))
    app.logger.addHandler(h)

# optional: align werkzeug log level too
logging.getLogger("werkzeug").setLevel(log_level)

# Configure app (ensure your config does NOT set SERVER_NAME)
cfg_key = os.getenv("PIPELINE_CONFIG") or "default"
config = config_module.options[cfg_key]
app.config.from_object(config)

# Prefer correct external URL scheme when generating absolute URLs.
# If you terminate TLS at nginx:443, set "https"; otherwise leave "http".
app.config.setdefault("PREFERRED_URL_SCHEME", "http")

# Trust one proxy hop (nginx) for host/proto/port so url_for() builds correct absolute URLs
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
    x_port=1,
    x_prefix=1,
)

# Register extensions
bootstrap = Bootstrap(app)
if not (app.debug or app.testing or app.config.get("SSL_DISABLE", False)):
    from flask_sslify import SSLify
    sslify = SSLify(app)

# Register blueprints
from .main import main as main_blueprint
from .images import images as image_blueprint
app.register_blueprint(main_blueprint)
app.register_blueprint(image_blueprint)
