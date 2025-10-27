import os

try:
    from flask import Flask
except ImportError:
    os.system("pip install Flask")
    from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Bot en ligne et health check OK"

def keep_alive():
    app.run(host="0.0.0.0", port=8080)
