import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'extremely hard to guess code string'
    SSL_DISABLE = True

class DevelopmentConfig(Config):
    DEBUG = True
    # SERVER_NAME removed to avoid host/port mismatch behind Nginx

class ProductionConfig(Config):
    DEBUG = False  # this won't work with flask script, use Flask.run() instead
    # SERVER_NAME removed to avoid host/port mismatch behind Nginx

options = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig,
}