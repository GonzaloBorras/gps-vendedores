import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

os.environ.setdefault('DATABASE_URL', '')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('DASH_PIN', '1234')

from app import app as flask_app

@pytest.fixture
def app():
    flask_app.config['TESTING'] = True
    flask_app.config['SESSION_COOKIE_SECURE'] = False
    yield flask_app

@pytest.fixture
def client(app):
    return app.test_client()
