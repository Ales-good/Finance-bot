web: gunicorn bot:flask_app --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 120
worker: python bot_runner.py
